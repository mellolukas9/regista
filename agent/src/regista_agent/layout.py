"""Who may do what under `%ProgramData%\\Regista`: the permission matrix as code (ADR 0022).

Three identities matter. The **agent** (`NT SERVICE\\RegistaAgent`) holds the machine's key and
talks to the server. The **robot** (the identity of the robot host: `NT SERVICE\\RegistaRobot` in
`service` mode, the dedicated user in `session` mode) runs code that is not ours. SYSTEM and the
Administrators can do anything. Nobody else, local users included, gets in.

`apply` writes the matrix (what `regista-agent setup` does, and what makes a machine prepared by an
older version safe); `check` reads the real ACLs back and says where they differ (what `diagnose`
and the Windows tests use). Both come from the same table, so they cannot drift apart.

Levels: `-` nothing, `T` traverse only (reach a path, no listing, no reading), `R` read and
execute, `M` modify (create, write, delete), `F` full control.
"""

import shutil
import sys
from dataclasses import dataclass

from regista_agent.config import AgentSettings

# FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE: enough to reach and stat a folder (a runtime
# that resolves its own path asks for that), not enough to list or read it.
TRAVERSE_RIGHTS = "0x1000a0"
MODIFY_RIGHTS = "0x1301bf"
_RIGHTS = {"R": "FRFX", "M": MODIFY_RIGHTS, "F": "FA"}


@dataclass(frozen=True)
class Entry:
    rel: str  # path under the home; "" is the home itself
    kind: str  # "dir" (own protected ACL) or "file" (inherits from its folder)
    agent: str
    robot: str
    note: str = ""


# The order matters for `apply`: the root first, so what is inside gets its inheritable entries.
MATRIX: tuple[Entry, ...] = (
    Entry("", "dir", "R", "T", "raiz"),
    Entry("keys", "dir", "R", "-", "chave da máquina e identidade"),
    Entry("keys/machine.key", "file", "R", "-"),
    Entry("keys/identity.json", "file", "R", "-"),
    Entry("agent.toml", "file", "R", "-", "só um administrador altera"),
    Entry("PAUSED", "file", "R", "-", "kill switch, só um administrador altera"),
    Entry("logs", "dir", "M", "-"),
    Entry("packages", "dir", "M", "-", "zips em cache"),
    Entry("uv-cache", "dir", "M", "-"),
    Entry("python", "dir", "R", "R", "runtime compartilhado, escrita só do setup"),
    Entry("browsers", "dir", "R", "R", "runtime compartilhado, escrita só do setup"),
    Entry("runs", "dir", "F", "T", "uma subpasta por execução"),
)


def _ace(rights: str, sid: str, *, inherit: bool = True) -> str:
    return f"(A;{'OICI' if inherit else ''};{rights};;;{sid})"


def sddl_for(entry: Entry, agent_sid: str, robot_sid: str) -> str:
    """The protected DACL of one folder of the matrix."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    from regista_agent import _windows

    parts = [
        _ace("FA", _windows.SYSTEM_SID),
        _ace("FA", _windows.ADMINISTRATORS_SID),
    ]
    if entry.agent != "-":
        parts.append(_ace(_RIGHTS[entry.agent], agent_sid))
    if entry.robot == "T":
        parts.append(_ace(TRAVERSE_RIGHTS, robot_sid, inherit=False))
    elif entry.robot != "-":
        parts.append(_ace(_RIGHTS[entry.robot], robot_sid))
    return "D:P" + "".join(parts)


def lock_applies(settings: AgentSettings) -> bool:
    """Whether to write ACLs at all. Only a development agent whose robots are its own children
    (`REGISTA_DEV_DIRECT_ROBOT`, run by a person without elevation) would lock its own folders
    against that person, so there it is skipped. Whenever a robot host is involved, and in
    production, the permissions are always written."""
    from regista_agent import launcher, policy

    if sys.platform != "win32":
        return False
    return not (
        settings.environment == "dev"
        and not policy.is_elevated()
        and launcher.direct_allowed(settings)
    )


def apply(settings: AgentSettings, agent_sid: str, robot_sid: str) -> list[str]:
    """Create the folders and write the matrix. Idempotent. Returns what it removed (legacy)."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    from regista_agent import _windows

    removed: list[str] = []
    settings.home.mkdir(parents=True, exist_ok=True)
    folders = [settings.home / e.rel if e.rel else settings.home for e in MATRIX if e.kind == "dir"]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    # What older versions left behind is not trusted: an environment a robot could have changed,
    # a cache it could have poisoned. Both are made again, from the verified package, per run.
    # Done before the ACLs are written, while whoever runs this still reaches everything.
    legacy_envs = settings.home / "envs"
    if legacy_envs.exists():
        shutil.rmtree(legacy_envs, ignore_errors=True)
        removed.append(str(legacy_envs))
    if settings.uv_cache_dir.exists() and any(settings.uv_cache_dir.iterdir()):
        for child in settings.uv_cache_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        removed.append(str(settings.uv_cache_dir) + " (esvaziado)")
    for entry, folder in zip((e for e in MATRIX if e.kind == "dir"), folders, strict=True):
        _windows.apply_dacl(folder, sddl_for(entry, agent_sid, robot_sid))
        _windows.hand_ownership_to_administrators(folder)
    return removed


def check(settings: AgentSettings, agent_sid: str, robot_sid: str) -> list[str]:
    """Where the real ACLs differ from the matrix, in plain Portuguese. Empty means they match."""
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    from regista_agent import _windows

    problems: list[str] = []
    known = {_windows.SYSTEM_SID, _windows.ADMINISTRATORS_SID, agent_sid, robot_sid}
    for entry in MATRIX:
        path = settings.home / entry.rel if entry.rel else settings.home
        if not path.exists():
            if entry.rel == "":
                problems.append(f"A pasta {path} não existe.")
            continue
        label = entry.rel or "a pasta raiz"
        try:
            dacl = _windows.read_dacl(path)
        except OSError as exc:
            problems.append(str(exc))
            continue
        if entry.kind == "dir" and not dacl.protected:
            problems.append(f"{label} herda permissões da pasta de cima.")
        levels = {agent_sid: "-", robot_sid: "-"}
        for ace in dacl.aces:
            if ace.kind == "D":
                if ace.sid in (agent_sid, robot_sid):
                    problems.append(f"{label}: há uma regra de bloqueio para {ace.sid}.")
                continue
            if ace.sid not in known:
                problems.append(f"{label}: uma conta fora da lista tem acesso ({ace.sid}).")
                continue
            if ace.sid in levels and _windows.LEVELS.index(ace.level) > _windows.LEVELS.index(
                levels[ace.sid]
            ):
                levels[ace.sid] = ace.level
        for who, sid, allowed in (
            ("a conta do agente", agent_sid, entry.agent),
            ("a conta do robô", robot_sid, entry.robot),
        ):
            order = _windows.LEVELS
            actual = levels[sid]
            if order.index(actual) > order.index(allowed):
                problems.append(
                    f"{label}: {who} tem acesso demais ({actual}; o certo é {allowed})."
                )
            elif order.index(actual) < order.index(allowed):
                problems.append(f"{label}: {who} não tem o acesso necessário ({allowed}).")
    return problems


def separate_identities(settings: AgentSettings) -> bool:
    """Is the robot a different identity from the agent (it runs through the robot host)? Only
    then do the folders of a run need their own permissions. A robot that is the agent's own child
    (development, outside Windows) has nobody to be separated from."""
    from regista_agent import launcher

    return sys.platform == "win32" and not launcher.direct_allowed(settings)


def identities(settings: AgentSettings) -> tuple[str | None, str | None]:
    """(agent SID, robot SID) when the run folders get their own permissions, else (None, None)."""
    if not separate_identities(settings):
        return None, None
    from regista_agent import _windows
    from regista_agent.config import DEFAULT_SERVICE_ACCOUNT

    robot_account = settings.effective_robot_account
    if robot_account is None:
        return None, None
    return (
        _windows.resolve_sid(settings.agent_account or DEFAULT_SERVICE_ACCOUNT),
        _windows.resolve_sid(robot_account),
    )
