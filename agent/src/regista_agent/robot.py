"""Running a robot as a child process (docs/specs/agent.md, "Execução de um robô").

No shell, ever: the child is `python -u main.py` started from a list of arguments, in the robot's
own folder, with an environment built from an allowlist. Nothing of the agent's own configuration
or credentials reaches the robot: no token, no `REGISTA_*` variable of the agent.

In M3 the robot comes from a local folder, and only when the agent is explicitly in development
mode (`REGISTA_DEV_UNSIGNED=1` with `REGISTA_ENVIRONMENT=dev`, see `config.py`). Signed packages and
an environment per version with `uv` arrive in M4.
"""

import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal

import psutil

PACKAGE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
ENTRY_POINT = "main.py"

# What a robot may inherit. Proxy and certificate settings are not secrets and a robot behind a
# corporate proxy needs them; everything else from the agent's environment stays out.
_INHERITED = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "HOME",
    "USERNAME",
    "USER",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
    "LANG",
    "LC_ALL",
    "DISPLAY",
    "XDG_RUNTIME_DIR",
    "PLAYWRIGHT_BROWSERS_PATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)

Priority = Literal["below_normal", "normal"]


def resolve_dev_robot(bots_dir: Path | None, package_name: str) -> Path | None:
    """`<bots_dir>/<package_name>/main.py`, or None. The name comes from the server, so it is
    checked here too and the resolved path must stay inside `bots_dir`."""
    if bots_dir is None or not PACKAGE_NAME.fullmatch(package_name):
        return None
    root = bots_dir.resolve()
    entry = (root / package_name / ENTRY_POINT).resolve()
    if not entry.is_relative_to(root) or not entry.is_file():
        return None
    return entry


def build_env(
    *,
    base: Mapping[str, str],
    job_id: str,
    params_json: str,
    artifacts_dir: Path,
    cancel_file: Path,
    temp_dir: Path,
) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k in _INHERITED or k.upper() in _INHERITED}
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "TMPDIR": str(temp_dir),
            "REGISTA_JOB_ID": job_id,
            "REGISTA_JOB_PARAMS": params_json,
            "REGISTA_ARTIFACTS_DIR": str(artifacts_dir),
            "REGISTA_CANCEL_FILE": str(cancel_file),
        }
    )
    return env


def _creation_flags(priority: Priority) -> int:
    if sys.platform != "win32":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP  # lets Ctrl+Break reach the robot alone
    if priority == "below_normal":
        flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return flags


@dataclass
class RobotProcess:
    """A running robot and the threads that read its output line by line."""

    popen: "subprocess.Popen[bytes]"
    cancel_file: Path
    readers: list[threading.Thread] = field(default_factory=list)

    @property
    def pid(self) -> int:
        return self.popen.pid

    def poll(self) -> int | None:
        return self.popen.poll()

    def join_readers(self, timeout: float = 5.0) -> None:
        for reader in self.readers:
            reader.join(timeout)

    def stop(self, *, grace_seconds: float) -> None:
        """Ask the robot to stop, give it `grace_seconds`, then kill it and everything it
        started (the browser included). The cancel file is for robots that look at it between
        items; the signal is for the rest."""
        try:
            self.cancel_file.write_text("cancel", encoding="utf-8")
        except OSError:
            pass
        if self.popen.poll() is None:
            try:
                if sys.platform == "win32":
                    self.popen.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.popen.send_signal(signal.SIGTERM)
            except (OSError, ValueError):
                pass
            deadline = time.monotonic() + grace_seconds
            while self.popen.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
        self.kill_tree()

    def kill_tree(self) -> None:
        try:
            parent = psutil.Process(self.popen.pid)
            victims = [*parent.children(recursive=True), parent]
        except psutil.NoSuchProcess:
            victims = []
        for victim in victims:
            try:
                victim.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(victims, timeout=5)
        try:
            self.popen.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover  (a process that survives SIGKILL)
            pass


def _pump(stream: IO[bytes], emit: Callable[[str], None]) -> None:
    for raw in iter(stream.readline, b""):
        emit(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    stream.close()


def start(
    *,
    python: str,
    entry: Path,
    env: Mapping[str, str],
    cancel_file: Path,
    priority: Priority,
    on_stdout: Callable[[str], None],
    on_stderr: Callable[[str], None],
) -> RobotProcess:
    popen = subprocess.Popen(  # noqa: S603  (a fixed argument list, no shell, a checked path)
        [python, "-u", str(entry)],
        cwd=entry.parent,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=_creation_flags(priority),
    )
    if sys.platform != "win32" and priority == "below_normal":
        try:
            psutil.Process(popen.pid).nice(10)
        except (psutil.Error, OSError):
            pass
    streams = [(popen.stdout, on_stdout), (popen.stderr, on_stderr)]
    process = RobotProcess(popen=popen, cancel_file=cancel_file)
    for stream, emit in streams:
        if stream is None:  # not possible with PIPE; keeps the types honest
            continue
        thread = threading.Thread(target=_pump, args=(stream, emit), daemon=True)
        thread.start()
        process.readers.append(thread)
    return process
