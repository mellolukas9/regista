"""The robot host: the process that starts robots, and runs as the robot's identity (ADR 0022).

It holds **no credential**: it does not import the key store, the identity, or the server client
(a test checks it). It connects to the pipe the agent serves, says hello, and then only obeys what
the agent sends: run, cancel, ping. It reports what happened to the robot (started, output,
exited) and asks for nothing. It also does not trust the agent's paths blindly: a robot runs only
from inside the run folder named in the message.

Every robot is held in a Job Object (Windows) that dies with the host, so no robot outlives it.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any, Protocol

import psutil

from regista_agent import hostproto
from regista_agent.hostproto import (
    Cancel,
    Exited,
    Hello,
    Output,
    Ping,
    Pong,
    ProtocolViolation,
    Run,
    RunSpec,
    Started,
    StartFailed,
)
from regista_agent.launcher import Connection

log = logging.getLogger("regista_agent.host")

# Profile variables the host fills in from its own environment when the agent leaves them out
# (`session` mode: the dedicated user's own profile is the robot's).
PROFILE_VARIABLES = (
    "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "USERNAME", "USER",
)  # fmt: skip


class Containment(Protocol):
    """Starts a robot and can kill it with everything it started."""

    def start(self, args: list[str], **kwargs: Any) -> "subprocess.Popen[bytes]": ...

    def kill(self) -> None: ...

    def close(self) -> None: ...


class TreeContainment:
    """Contains by walking the process tree (outside Windows, and in tests). Weaker than a Job
    Object: a process that detached from its parent is not found."""

    def __init__(self) -> None:
        self._popen: subprocess.Popen[bytes] | None = None

    def start(self, args: list[str], **kwargs: Any) -> "subprocess.Popen[bytes]":
        self._popen = subprocess.Popen(args, **kwargs)  # noqa: S603  (a fixed list, no shell)
        return self._popen

    def kill(self) -> None:
        if self._popen is None:
            return
        try:
            parent = psutil.Process(self._popen.pid)
            victims = [*parent.children(recursive=True), parent]
        except psutil.NoSuchProcess:
            return
        for victim in victims:
            try:
                victim.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(victims, timeout=5)

    def close(self) -> None:
        self.kill()


class _JobContainment:
    """Windows: a Job Object (see `winjob`)."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows only")
        from regista_agent import winjob

        self._job: Any = winjob.Job()

    def start(self, args: list[str], **kwargs: Any) -> "subprocess.Popen[bytes]":
        popen: subprocess.Popen[bytes] = self._job.start(args, **kwargs)
        return popen

    def kill(self) -> None:
        self._job.terminate()

    def close(self) -> None:
        self._job.close()


def default_containment() -> Containment:
    return _JobContainment() if sys.platform == "win32" else TreeContainment()


def _creation_flags(priority: str) -> int:
    if sys.platform != "win32":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP  # lets Ctrl+Break reach the robot alone
    if priority == "below_normal":
        flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return flags


class _Active:
    def __init__(
        self, run_id: str, popen: "subprocess.Popen[bytes]", containment: Containment
    ) -> None:
        self.run_id = run_id
        self.popen = popen
        self.containment = containment
        self.done = threading.Event()


class HostCore:
    """The host's side of one connection to the agent."""

    def __init__(
        self,
        conn: Connection,
        *,
        runs_root: Path,
        containment: Callable[[], Containment] = default_containment,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._conn = conn
        self._runs_root = runs_root
        self._new_containment = containment
        self._environ = dict(os.environ if environ is None else environ)
        self._send_lock = threading.Lock()
        self._active: _Active | None = None

    # --- sending ------------------------------------------------------------------------------

    def _send(self, message: hostproto.HostMessage) -> None:
        with self._send_lock:
            self._conn.send(hostproto.encode_host(message), timeout=10.0)

    # --- the loop -----------------------------------------------------------------------------

    def serve(self) -> None:
        """Greet the agent and obey until the connection ends. Whatever robot is still running
        when it does is killed: no robot outlives its host's connection to the agent."""
        self._send(Hello(hostproto.PROTOCOL_VERSION))
        try:
            while True:
                try:
                    raw = self._conn.receive(timeout=1.0)
                except TimeoutError:
                    continue
                message = hostproto.decode_agent(raw)
                if isinstance(message, Ping):
                    self._send(Pong())
                elif isinstance(message, Run):
                    self._start(message.spec)
                elif isinstance(message, Cancel):
                    self._cancel(message)
        finally:
            active = self._active
            if active is not None and not active.done.is_set():
                active.containment.kill()
                active.containment.close()

    # --- starting a robot ---------------------------------------------------------------------

    def _refuse(self, run_id: str, reason: str) -> None:
        log.warning("run %s refused: %s", run_id, reason)
        self._send(StartFailed(run_id, reason[: hostproto.MAX_REASON_CHARS]))

    def _start(self, spec: RunSpec) -> None:
        if self._active is not None and not self._active.done.is_set():
            raise ProtocolViolation("o agente pediu uma execução com outra em andamento")
        run_dir = self._runs_root / spec.run_id
        if not run_dir.is_dir():
            return self._refuse(spec.run_id, "a pasta da execução não existe")
        for what, path, parent in (
            ("o Python", spec.python, run_dir / "venv"),
            ("o código", spec.entry, run_dir / "package"),
            ("a pasta de trabalho", spec.cwd, run_dir / "package"),
        ):
            if not hostproto.within(path, parent):
                return self._refuse(spec.run_id, f"{what} não está dentro da pasta da execução")
        env = dict(spec.env)
        for name in PROFILE_VARIABLES:
            if name not in env and name in self._environ:
                env[name] = self._environ[name]
        containment = self._new_containment()
        try:
            popen = containment.start(
                [spec.python, "-u", spec.entry],
                cwd=spec.cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=_creation_flags(spec.priority),
            )
        except (OSError, ValueError) as exc:
            containment.close()
            return self._refuse(spec.run_id, f"{type(exc).__name__}: {exc}")
        active = _Active(spec.run_id, popen, containment)
        self._active = active
        self._send(Started(spec.run_id, popen.pid))
        pumps = [
            threading.Thread(target=self._pump, args=(spec.run_id, "stdout", popen.stdout)),
            threading.Thread(target=self._pump, args=(spec.run_id, "stderr", popen.stderr)),
        ]
        for pump in pumps:
            pump.daemon = True
            pump.start()
        threading.Thread(target=self._watch, args=(active, pumps), daemon=True).start()

    def _pump(self, run_id: str, stream: Any, source: IO[bytes] | None) -> None:
        if source is None:
            return
        try:
            for raw in iter(source.readline, b""):
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                encoded = text.encode("utf-8")
                if len(encoded) > hostproto.MAX_LINE_BYTES:  # the host cuts, never the agent
                    text = encoded[: hostproto.MAX_LINE_BYTES - 8].decode("utf-8", errors="ignore")
                self._send(Output(run_id, stream, text))
        except (OSError, ValueError):
            pass  # the connection is gone; `serve` is already ending
        finally:
            source.close()

    def _watch(self, active: _Active, pumps: list[threading.Thread]) -> None:
        code = active.popen.wait()
        for pump in pumps:
            pump.join(5.0)
        active.containment.kill()  # whatever the robot left behind
        active.containment.close()
        try:
            self._send(Exited(active.run_id, code & 0xFFFFFFFF if code >= 0 else code))
        except OSError:
            pass
        finally:
            active.done.set()

    # --- stopping a robot ---------------------------------------------------------------------

    def _cancel(self, message: Cancel) -> None:
        active = self._active
        if active is None or active.done.is_set():
            return
        if message.run_id != active.run_id:
            raise ProtocolViolation("cancelamento de outra execução")
        threading.Thread(
            target=self._stop, args=(active, message.grace_seconds), daemon=True
        ).start()

    def _stop(self, active: _Active, grace: int) -> None:
        if grace > 0 and active.popen.poll() is None:
            try:
                if sys.platform == "win32":
                    active.popen.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    active.popen.send_signal(signal.SIGTERM)
            except (OSError, ValueError):
                pass
            deadline = time.monotonic() + grace
            while active.popen.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
        if not active.done.is_set():
            active.containment.kill()


def run_host(
    stop: threading.Event,
    *,
    runs_root: Path | None = None,
    agent_service: str | None = None,
    pipe: str | None = None,
) -> None:
    """The host's life on Windows: connect to the agent's pipe, check that the server really is the
    agent (the service manager's process id for the agent's service must be the pipe server's),
    serve until the connection ends, and do it again. It never gives up while `stop` is clear: the
    agent may be restarted, and the host comes back by itself."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    from regista_agent import winpipe
    from regista_agent.config import AGENT_SERVICE_NAME, ROBOT_HOST_PIPE, default_home

    root = runs_root or default_home() / "runs"
    service = agent_service or AGENT_SERVICE_NAME
    name = pipe or ROBOT_HOST_PIPE
    while not stop.is_set():
        try:
            connection = winpipe.connect(name, timeout=5.0)
        except (TimeoutError, OSError) as exc:
            log.debug("agent pipe not available yet: %s", exc)
            stop.wait(2.0)
            continue
        try:
            if not winpipe.server_is_service(connection, service):
                log.warning("the pipe server is not the %s service: not trusted", service)
                stop.wait(5.0)
                continue
            log.info("connected to the agent")
            HostCore(connection, runs_root=root).serve()
        except (OSError, ProtocolViolation) as exc:
            log.warning("connection to the agent ended: %s", exc)
        finally:
            connection.close()
        stop.wait(1.0)
