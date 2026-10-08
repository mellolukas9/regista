"""How the agent starts a robot (ADR 0022): through the robot host, or directly.

* `HostLauncher` (the one used on Windows, always): asks the **robot host** to start the robot, over
  the pipe the agent serves. The host runs as the robot's own identity, so the robot never has the
  agent's. If the host is not there, does not answer, is not who it should be, or breaks the
  protocol, the run fails with `robot_host_unavailable`: there is **no fallback** to running the
  robot as the agent.
* `DirectLauncher` (development and tests, and Linux, which is outside the MVP): the robot is a
  child of the agent, under the agent's own account. Production on Windows refuses it
  (`AgentSettings.check_dev_direct`).

Both hand `JobExecutor` the same small handle, so supervising a run does not care which it is.
"""

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from regista_agent import hostproto, robot
from regista_agent.config import AgentSettings
from regista_agent.hostproto import (
    Cancel,
    Exited,
    HostConversation,
    Output,
    Ping,
    Pong,
    ProtocolViolation,
    Run,
    RunSpec,
    Started,
    StartFailed,
)

log = logging.getLogger("regista_agent")

HOST_UNAVAILABLE = "robot_host_unavailable"
START_TIMEOUT_SECONDS = 20.0  # from `run` to `started`
SILENCE_PING_SECONDS = 5.0  # a quiet host is asked if it is alive
SILENCE_LIMIT_SECONDS = 20.0  # and is given up on after this much silence


class LaunchFailed(Exception):
    """The robot could not be started. `detail` is for the logs of the run only."""

    def __init__(self, detail: str, code: str = HOST_UNAVAILABLE) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


@dataclass(frozen=True)
class LaunchSpec:
    """What it takes to start a robot: all paths are inside the run folder."""

    run_id: str
    python: str
    entry: Path
    env: dict[str, str]
    cancel_file: Path
    priority: str


class RobotHandle(Protocol):
    @property
    def lost(self) -> bool:
        """True when the robot can no longer be supervised (the host went away or broke the
        protocol). The robots of a lost host are killed by the host's job object."""
        ...

    def poll(self) -> int | None: ...

    def join_readers(self, timeout: float = 5.0) -> None: ...

    def stop(self, *, grace_seconds: float) -> None: ...

    def kill_tree(self) -> None: ...


class Launcher(Protocol):
    def start(
        self,
        spec: LaunchSpec,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
    ) -> RobotHandle: ...


def direct_allowed(settings: AgentSettings) -> bool:
    """Whether the robot may be started under the agent's own account. Only outside Windows (not
    in the MVP), or in development with the explicit flag, or for a robot from a development
    folder. Production on Windows never."""
    settings.check_dev_direct()
    return (
        sys.platform != "win32"
        or settings.dev_direct_robot
        or (settings.dev_unsigned and settings.environment == "dev")
    )


# --- direct -------------------------------------------------------------------------------------


class _DirectHandle:
    def __init__(self, process: robot.RobotProcess) -> None:
        self._process = process
        self.lost = False

    def poll(self) -> int | None:
        return self._process.poll()

    def join_readers(self, timeout: float = 5.0) -> None:
        self._process.join_readers(timeout)

    def stop(self, *, grace_seconds: float) -> None:
        self._process.stop(grace_seconds=grace_seconds)

    def kill_tree(self) -> None:
        self._process.kill_tree()


class DirectLauncher:
    def start(
        self,
        spec: LaunchSpec,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
    ) -> RobotHandle:
        process = robot.start(
            python=spec.python,
            entry=spec.entry,
            env=spec.env,
            cancel_file=spec.cancel_file,
            priority=spec.priority,  # type: ignore[arg-type]
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )
        return _DirectHandle(process)


# --- through the host ---------------------------------------------------------------------------


class Connection(Protocol):
    """One connected host, as the agent sees it (the pipe, or a stand-in in tests)."""

    def send(self, payload: bytes, *, timeout: float = 10.0) -> None: ...

    def receive(self, *, timeout: float = 10.0) -> bytes: ...

    def close(self) -> None: ...


class StaleConnection(Exception):
    """The host that was lent was already gone: nothing was started, another one may be tried."""


class HostChannel:
    """Keeps the one connection of the host and lends it to one run at a time.

    `accept` is blocking and returns a **verified** connection (the pipe checks who connected
    before it gets here). The channel then reads the host's `hello`; a host that does not greet
    properly is dropped. A connection that breaks is dropped too, and the next one is awaited: the
    host reconnects by itself."""

    def __init__(self, accept: Callable[[], Connection | None]) -> None:
        self._accept = accept
        self._ready: queue.Queue[tuple[Connection, HostConversation]] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="robot-host-channel", daemon=True)
        self._returned = threading.Condition()
        self._dropped: set[int] = set()
        self._connected = threading.Event()

    def start(self) -> None:
        self._thread.start()

    @property
    def connected(self) -> bool:
        """Is a verified host connected right now (idle or running a robot)?"""
        return self._connected.is_set()

    def close(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn = self._accept()
            except Exception as exc:  # the pipe is unusable: say so and try again
                if not isinstance(exc, TimeoutError):
                    log.warning("robot host channel: %s", exc)
                    self._stop.wait(1.0)
                continue
            if conn is None:
                continue
            conversation = HostConversation()
            try:
                conversation.accept(conn.receive(timeout=10.0))  # the hello
            except Exception as exc:
                log.warning("robot host refused: it did not greet properly (%s)", exc)
                conn.close()
                continue
            log.info("robot host connected")
            self._ready.put((conn, conversation))
            self._connected.set()
            with self._returned:
                while not self._stop.is_set() and id(conn) not in self._dropped:
                    self._returned.wait(0.5)
                self._dropped.discard(id(conn))
            self._connected.clear()
            log.info("robot host disconnected")

    def acquire(self, timeout: float) -> tuple[Connection, HostConversation] | None:
        """The connected host, or None if none connects in `timeout` seconds."""
        try:
            return self._ready.get(timeout=timeout)
        except queue.Empty:
            return None

    def release(self, conn: Connection, conversation: HostConversation, *, healthy: bool) -> None:
        """Give the host back. A healthy one is offered to the next run; any other is closed (the
        host reconnects) and the next run waits for it."""
        if healthy and conversation.idle:
            self._ready.put((conn, conversation))
            return
        conn.close()
        with self._returned:
            self._dropped.add(id(conn))
            self._returned.notify_all()


class _HostHandle:
    """A run being supervised through the host."""

    def __init__(
        self,
        channel: HostChannel,
        conn: Connection,
        conversation: HostConversation,
        spec: LaunchSpec,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
    ) -> None:
        self._channel = channel
        self._conn = conn
        self._conversation = conversation
        self._spec = spec
        self._emit = {"stdout": on_stdout, "stderr": on_stderr}
        self._code: int | None = None
        self._lost = threading.Event()
        self._finished = threading.Event()
        self._reader = threading.Thread(target=self._read, name="robot-host-reader", daemon=True)
        self._send_lock = threading.Lock()
        self.reason = ""

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def poll(self) -> int | None:
        return self._code

    def join_readers(self, timeout: float = 5.0) -> None:
        self._finished.wait(timeout)

    def _send(self, message: hostproto.AgentMessage) -> None:
        with self._send_lock:
            self._conn.send(hostproto.encode_agent(message), timeout=10.0)

    # --- the start ----------------------------------------------------------------------------

    def begin(self) -> None:
        spec = self._spec
        self._conversation.begin_run(spec.run_id)
        try:
            self._send_run(spec)
        except OSError as exc:
            raise StaleConnection(str(exc)) from exc
        try:
            raw = self._conn.receive(timeout=START_TIMEOUT_SECONDS)
        except TimeoutError:
            raise
        except OSError as exc:
            # The connection is closed: the host is gone, and with it every robot it held (job
            # object), so nothing runs and another host may be tried. Silence is different: a
            # host that is alive but quiet might have started the robot, so that is a failure.
            raise StaleConnection(str(exc)) from exc
        first = self._conversation.accept(raw)
        if isinstance(first, StartFailed):
            raise LaunchFailed(f"o hospedeiro não conseguiu iniciar o robô: {first.reason}")
        if not isinstance(first, Started):
            raise ProtocolViolation("o hospedeiro deveria responder started")
        self._reader.start()

    def _send_run(self, spec: LaunchSpec) -> None:
        self._send(
            Run(
                RunSpec(
                    run_id=spec.run_id,
                    python=spec.python,
                    entry=str(spec.entry),
                    cwd=str(spec.entry.parent),
                    env=spec.env,
                    priority=spec.priority,
                )
            )
        )

    # --- reading ------------------------------------------------------------------------------

    def _read(self) -> None:
        last_seen = time.monotonic()
        last_ping = 0.0
        try:
            while not self._finished.is_set():
                try:
                    raw = self._conn.receive(timeout=1.0)
                except Exception as exc:
                    if not isinstance(exc, TimeoutError):
                        raise
                    now = time.monotonic()
                    if now - last_seen > SILENCE_LIMIT_SECONDS:
                        raise LaunchFailed("o hospedeiro parou de responder") from None
                    if now - last_seen > SILENCE_PING_SECONDS and now - last_ping > 2.0:
                        self._conversation.ping_sent()
                        self._send(Ping())
                        last_ping = now
                    continue
                last_seen = time.monotonic()
                message = self._conversation.accept(raw)
                if isinstance(message, Output):
                    self._emit[message.stream](message.line)
                elif isinstance(message, Exited):
                    self._code = message.code
                    break
                elif not isinstance(message, Pong):  # hello/started again: accept() refused them
                    raise ProtocolViolation("mensagem inesperada")
        except Exception as exc:
            self.reason = str(exc)
            log.warning("robot host lost during a run: %s", exc)
            self._lost.set()
        finally:
            self._finished.set()

    # --- stopping -----------------------------------------------------------------------------

    def stop(self, *, grace_seconds: float) -> None:
        """Ask the robot to stop (the cancel file and a signal through the host), and have the host
        kill the whole tree after `grace_seconds`."""
        try:
            self._spec.cancel_file.write_text("cancel", encoding="utf-8")
        except OSError:
            pass
        self._cancel(int(grace_seconds))
        self._finished.wait(grace_seconds + 10.0)
        if not self._finished.is_set():
            self._lost.set()  # it did not obey: the host is not to be trusted any more

    def kill_tree(self) -> None:
        if self._code is None and not self._lost.is_set():
            self._cancel(0)
            self._finished.wait(10.0)

    def _cancel(self, grace: int) -> None:
        try:
            self._send(Cancel(self._spec.run_id, max(0, min(grace, hostproto.MAX_GRACE_SECONDS))))
        except Exception as exc:
            log.warning("cancel could not be sent to the host: %s", exc)
            self._lost.set()

    def release(self) -> None:
        healthy = self._code is not None and not self._lost.is_set()
        self._channel.release(self._conn, self._conversation, healthy=healthy)


class HostLauncher:
    def __init__(self, channel: HostChannel, *, wait_seconds: float = 15.0) -> None:
        self._channel = channel
        self._wait = wait_seconds

    def start(
        self,
        spec: LaunchSpec,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
    ) -> RobotHandle:
        deadline = time.monotonic() + self._wait
        while True:
            lent = self._channel.acquire(max(0.0, deadline - time.monotonic()))
            if lent is None:
                raise LaunchFailed("o hospedeiro do robô não está conectado")
            conn, conversation = lent
            handle = _HostHandle(self._channel, conn, conversation, spec, on_stdout, on_stderr)
            try:
                handle.begin()
            except StaleConnection:
                # The host went away while idle (a restart between two runs): it never got the
                # run, so waiting for it to reconnect is safe.
                self._channel.release(conn, conversation, healthy=False)
                continue
            except LaunchFailed:
                self._channel.release(conn, conversation, healthy=conversation.idle)
                raise
            except Exception as exc:  # silence, a broken pipe, a violation
                self._channel.release(conn, conversation, healthy=False)
                raise LaunchFailed(f"o hospedeiro não respondeu como esperado: {exc}") from exc
            return _ReleasingHandle(handle)


class _ReleasingHandle:
    """Gives the host back to the channel the first time the run is known to be over."""

    def __init__(self, inner: _HostHandle) -> None:
        self._inner = inner
        self._released = False

    @property
    def lost(self) -> bool:
        return self._inner.lost

    @property
    def reason(self) -> str:
        return self._inner.reason

    def _maybe_release(self) -> None:
        if not self._released and (self._inner.poll() is not None or self._inner.lost):
            self._released = True
            self._inner.release()

    def poll(self) -> int | None:
        code = self._inner.poll()
        if code is not None or self._inner.lost:
            self._inner.join_readers(0.0)
            self._maybe_release()
        return code

    def join_readers(self, timeout: float = 5.0) -> None:
        self._inner.join_readers(timeout)
        self._maybe_release()

    def stop(self, *, grace_seconds: float) -> None:
        self._inner.stop(grace_seconds=grace_seconds)
        self._maybe_release()

    def kill_tree(self) -> None:
        self._inner.kill_tree()
        self._maybe_release()
