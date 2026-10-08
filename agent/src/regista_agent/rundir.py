r"""The folder of one run: `runs\<id>\` (ADR 0022).

Everything a run needs and everything it leaves lives here, and the agent deletes it at the end,
whatever the outcome. Layout and who may do what (the robot is the account of the robot host):

    runs\<id>\             agent: full; robot: reach (and read the `cancel` file)
        build\             agent only: the extracted package (wheels, lock)
        package\           robot reads and runs: the code of this version
        venv\              robot reads and runs: the environment made for this run
        tmp\               robot writes: TEMP, HOME and the fake profile
        artifacts\         robot writes: screenshots and other files (untrusted when read back)
        cancel             written by the agent; the robot reads it

The permissions of each folder are written **before** anything is put in it, so what lands there
is born with the right ones (no walk over thousands of files afterwards). The folders the robot
writes in also take away the owner's implicit right to change the ACL, so a robot cannot lock the
agent out of what it created (and cannot hand itself, or anyone, more access).
"""

import logging
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from regista_agent import layout, safefs
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError

log = logging.getLogger("regista_agent")

_NAME_BYTES = 6  # 12 hex characters: Windows paths are limited to 260 and site-packages is deep
_OWNER_RIGHTS_READ_ONLY = "(A;OICI;0x20000;;;OW)"  # owners: read the ACL, never change it


@dataclass(frozen=True)
class RunDir:
    root: Path

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def package(self) -> Path:
        return self.root / "package"

    @property
    def venv(self) -> Path:
        return self.root / "venv"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def cancel_file(self) -> Path:
        return self.root / "cancel"

    @property
    def name(self) -> str:
        return self.root.name


def _dacls(agent_sid: str, robot_sid: str) -> dict[str, str]:
    """Protected DACLs by sub-folder name ("" is the run folder itself)."""
    from regista_agent import _windows

    base = (
        f"(A;OICI;FA;;;{_windows.SYSTEM_SID})"
        f"(A;OICI;FA;;;{_windows.ADMINISTRATORS_SID})"
        f"(A;OICI;FA;;;{agent_sid})"
    )
    read = f"(A;OICI;FRFX;;;{robot_sid})"
    write = f"(A;OICI;{layout.MODIFY_RIGHTS};;;{robot_sid})" + _OWNER_RIGHTS_READ_ONLY
    return {
        # The run folder: the robot can reach what is beneath it, never list it; the `cancel`
        # file inside is the one thing it may read (inherit-only, files only).
        "": f"D:P{base}(A;;{layout.TRAVERSE_RIGHTS};;;{robot_sid})(A;OIIO;FRFX;;;{robot_sid})",
        "build": f"D:P{base}",
        "package": f"D:P{base}{read}",
        "venv": f"D:P{base}{read}",
        "tmp": f"D:P{base}{write}",
        "artifacts": f"D:P{base}{write}",
    }


def create(
    settings: AgentSettings, agent_sid: str | None = None, robot_sid: str | None = None
) -> RunDir:
    """A new, empty run folder with its permissions already in place."""
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    for _attempt in range(5):
        root = settings.runs_dir / secrets.token_hex(_NAME_BYTES)
        try:
            root.mkdir()
        except FileExistsError:
            continue
        break
    else:  # pragma: no cover  (five collisions of 48 random bits)
        raise AgentError("Não foi possível criar a pasta da execução.")
    run = RunDir(root)
    lock = layout.separate_identities(settings)
    if lock and (agent_sid is None or robot_sid is None):
        safefs.remove_tree(root)
        raise AgentError("Faltam as contas do agente e do robô para preparar a execução.")
    try:
        subfolders = ("build", "package", "venv", "tmp", "artifacts")
        if lock:
            if sys.platform != "win32":
                raise RuntimeError("Windows only")
            from regista_agent import _windows

            assert agent_sid is not None and robot_sid is not None  # noqa: S101  (checked above)
            dacls = _dacls(agent_sid, robot_sid)
            _windows.apply_dacl(root, dacls[""])
            for name in subfolders:
                (root / name).mkdir()
                _windows.apply_dacl(root / name, dacls[name])
        else:
            for name in subfolders:
                (root / name).mkdir()
    except BaseException:
        safefs.remove_tree(root)
        raise
    return run


def remove(run: RunDir) -> None:
    """Delete the run folder, links not followed. A leftover is logged (the sweep at the next
    start of the agent tries again), never raised: the run's outcome does not depend on it."""
    if not safefs.remove_tree(run.root):
        log.warning("run folder %s could not be fully removed", run.root)


def sweep(settings: AgentSettings) -> None:
    """At startup, when no run is on: whatever is under `runs` is the leftover of a crash."""
    if not settings.runs_dir.is_dir():
        return
    for entry in settings.runs_dir.iterdir():
        if not safefs.remove_tree(entry):
            log.warning("leftover run folder %s could not be removed", entry)
