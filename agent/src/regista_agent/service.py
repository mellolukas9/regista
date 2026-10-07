"""`regista-agent service install | uninstall | status`: the minimal installer (ADR 0022).

Two Windows services and, in `session` mode, one scheduled task:

* `RegistaAgent`, account `NT SERVICE\\RegistaAgent`: talks to the server and holds the key;
* `RegistaRobot`, account `NT SERVICE\\RegistaRobot` (`service` mode): the robot host. In `session`
  mode this service is not installed; a **logon task** of the dedicated user starts the host in
  that user's desktop session instead (no password is stored anywhere: the trigger is the user's
  own logon).

Both services are virtual accounts (no password to keep or rotate). Needs an elevated console and
is idempotent. The program files must be changeable only by Administrators and SYSTEM, or a
robot (or anyone) could replace what the services run: the installer refuses a location that is
not, and a development checkout needs `--allow-insecure-path` and says so. The MSI of M8 replaces
this installer and installs under `Program Files`.
"""

import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from regista_agent import policy
from regista_agent.config import (
    AGENT_SERVICE_NAME,
    DEFAULT_ROBOT_ACCOUNT,
    DEFAULT_SERVICE_ACCOUNT,
    ROBOT_SERVICE_NAME,
    AgentSettings,
)
from regista_agent.errors import AgentError

TASK_NAME = "Regista\\RobotHost"
ERROR_SERVICE_DOES_NOT_EXIST = 1060

Run = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
Say = Callable[[str], None]


def _default_run(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603  (fixed argument lists, no shell)
        list(args), capture_output=True, text=True, check=False, errors="replace"
    )


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    display: str
    description: str
    account: str
    command: str  # the argument after `-m regista_agent`


def specs(mode: str) -> list[ServiceSpec]:
    agent = ServiceSpec(
        AGENT_SERVICE_NAME,
        "Regista Agent",
        "Liga esta máquina ao painel Regista: pega execuções e guarda a chave da máquina.",
        DEFAULT_SERVICE_ACCOUNT,
        "service run agent",
    )
    if mode == "session":
        return [agent]
    host = ServiceSpec(
        ROBOT_SERVICE_NAME,
        "Regista Robot Host",
        "Roda os robôs do Regista com uma conta própria, sem acesso à chave da máquina.",
        DEFAULT_ROBOT_ACCOUNT,
        "service run host",
    )
    return [agent, host]


def binary_path(python: Path, command: str) -> str:
    """The command line of a service, with the interpreter quoted (a path with spaces would
    otherwise let Windows run a shorter program: the classic unquoted service path)."""
    return f'"{python}" -m regista_agent {command}'


def task_xml(python: Path, user: str, command: str = "host --mode session") -> str:
    """The logon task of `session` mode: starts the host when `user` (and only that user) logs on,
    in their interactive session and without elevation. Nothing in it is a password."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hospedeiro do robô do Regista (ADR 0022).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(python))}</Command>
      <Arguments>-m regista_agent {escape(command)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


# --- program files --------------------------------------------------------------------------


def program_files(python: Path) -> list[Path]:
    """Everything a service starts from: the interpreter, what it is a copy of, and the package."""
    import regista_agent

    found = {python.parent, Path(getattr(sys, "_base_executable", python)).parent}
    found.add(Path(regista_agent.__file__).resolve().parent)
    return sorted(found)


def insecure_paths(python: Path) -> list[str]:
    """Where the program files can be changed by someone other than Administrators and SYSTEM."""
    if sys.platform != "win32":
        return []
    from regista_agent import _windows

    problems: list[str] = []
    for folder in program_files(python):
        problems.extend(_windows.writable_by_others(folder))
    return sorted(set(problems))


# --- install ----------------------------------------------------------------------------------


def _sc(run: Run, *args: str) -> "subprocess.CompletedProcess[str]":
    return run(["sc.exe", *args])


def exists(run: Run, name: str) -> bool:
    result = _sc(run, "query", name)
    return result.returncode != ERROR_SERVICE_DOES_NOT_EXIST


def _check(result: "subprocess.CompletedProcess[str]", what: str) -> None:
    if result.returncode != 0:
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        raise AgentError(f"{what} falhou: {detail}")


def install(
    settings: AgentSettings,
    *,
    mode: str,
    robot_account: str | None,
    python: Path | None = None,
    allow_insecure_path: bool = False,
    start: bool = False,
    say: Say = print,
    run: Run = _default_run,
) -> None:
    if sys.platform != "win32":
        raise AgentError("O instalador de serviços só existe no Windows.")
    policy.require_elevation(settings, "A instalação dos serviços")
    interpreter = Path(python or sys.executable).resolve()
    if mode not in ("service", "session"):
        raise AgentError("O modo precisa ser service ou session.")
    if mode == "session" and not robot_account:
        raise AgentError(
            "No modo Sessão informe o usuário dedicado: --robot-account <usuário>. É com a conta "
            "dele que o hospedeiro e os robôs rodam (sem senha guardada: a tarefa dispara no "
            "logon dele)."
        )
    problems = insecure_paths(interpreter)
    if problems:
        text = (
            "Os arquivos do programa podem ser alterados por contas que não são Administradores "
            "nem SYSTEM, e quem os altera passa a rodar o que quiser como o agente e o robô: "
            + "; ".join(problems[:6])
            + ". Instale o programa em uma pasta só de administradores (por exemplo, em "
            "C:\\Program Files)."
        )
        if not allow_insecure_path:
            raise AgentError(
                text + " Para um ambiente de desenvolvimento use --allow-insecure-path."
            )
        say("AVISO: " + text)

    for spec in specs(mode):
        _install_service(run, spec, interpreter, say)
    if mode == "session":
        _install_task(run, interpreter, str(robot_account), say)
    else:
        _remove_task(run, say, quiet=True)
    if start:
        for spec in specs(mode):
            _sc(run, "start", spec.name)
            say(f"Serviço {spec.name} iniciado.")


def _install_service(run: Run, spec: ServiceSpec, python: Path, say: Say) -> None:
    path = binary_path(python, spec.command)
    if exists(run, spec.name):
        _check(
            _sc(
                run,
                "config",
                spec.name,
                "binPath=",
                path,
                "obj=",
                spec.account,
                "start=",
                "auto",
                "DisplayName=",
                spec.display,
            ),
            f"Atualizar o serviço {spec.name}",
        )
        say(f"Serviço {spec.name} atualizado.")
    else:
        _check(
            _sc(
                run,
                "create",
                spec.name,
                "binPath=",
                path,
                "obj=",
                spec.account,
                "start=",
                "auto",
                "DisplayName=",
                spec.display,
            ),
            f"Criar o serviço {spec.name}",
        )
        say(f"Serviço {spec.name} criado ({spec.account}).")
    _sc(run, "description", spec.name, spec.description)
    # The account's SID is what ACLs name: it must exist as a service SID.
    _sc(run, "sidtype", spec.name, "unrestricted")
    # Restart after a crash (not after a clean stop, nor a revoked machine, which stops cleanly).
    _sc(
        run, "failure", spec.name, "reset=", "86400", "actions=",
        "restart/5000/restart/30000/restart/60000",
    )  # fmt: skip
    _sc(run, "failureflag", spec.name, "1")


def _install_task(run: Run, python: Path, user: str, say: Say) -> None:
    xml = task_xml(python, user)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "task.xml"
        path.write_text(xml, encoding="utf-16")
        result = run(["schtasks.exe", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"])
    _check(result, f"Criar a tarefa de logon {TASK_NAME}")
    say(f"Tarefa de logon {TASK_NAME} criada para {user}.")


def _remove_task(run: Run, say: Say, *, quiet: bool = False) -> None:
    result = run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode == 0:
        say(f"Tarefa {TASK_NAME} removida.")
    elif not quiet:
        say(f"Tarefa {TASK_NAME}: nada a remover.")


def uninstall(settings: AgentSettings, *, say: Say = print, run: Run = _default_run) -> None:
    if sys.platform != "win32":
        raise AgentError("O instalador de serviços só existe no Windows.")
    policy.require_elevation(settings, "A remoção dos serviços")
    for name in (ROBOT_SERVICE_NAME, AGENT_SERVICE_NAME):  # the host first, the agent after
        if exists(run, name):
            _sc(run, "stop", name)
            _check(_sc(run, "delete", name), f"Remover o serviço {name}")
            say(f"Serviço {name} removido.")
        else:
            say(f"Serviço {name}: nada a remover.")
    _remove_task(run, say)


# --- status -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    installed: bool
    state: str
    account: str
    command: str


def query(run: Run, name: str) -> ServiceStatus:
    result = _sc(run, "query", name)
    if result.returncode == ERROR_SERVICE_DOES_NOT_EXIST:
        return ServiceStatus(name, False, "", "", "")
    state = ""
    for line in (result.stdout or "").splitlines():
        if "STATE" in line:
            state = line.split(":", 1)[-1].split()[-1]
    config = _sc(run, "qc", name)
    account = command = ""
    for line in (config.stdout or "").splitlines():
        text = line.strip()
        if text.upper().startswith("SERVICE_START_NAME"):
            account = text.split(":", 1)[-1].strip()
        elif text.upper().startswith("BINARY_PATH_NAME"):
            command = text.split(":", 1)[-1].strip()
    return ServiceStatus(name, True, state, account, command)


def status(settings: AgentSettings, *, say: Say = print, run: Run = _default_run) -> None:
    if sys.platform != "win32":
        raise AgentError("O instalador de serviços só existe no Windows.")
    for name in (AGENT_SERVICE_NAME, ROBOT_SERVICE_NAME):
        info = query(run, name)
        if not info.installed:
            say(f"{name}: não instalado.")
            continue
        say(f"{name}: {info.state or '?'} | conta {info.account} | {info.command}")
    task = run(["schtasks.exe", "/Query", "/TN", TASK_NAME])
    say(f"Tarefa {TASK_NAME}: " + ("instalada." if task.returncode == 0 else "não instalada."))
    problems = insecure_paths(Path(sys.executable).resolve())
    if problems:
        say("AVISO: arquivos do programa alteráveis por outras contas: " + "; ".join(problems[:6]))
    else:
        say("Arquivos do programa: só Administradores e SYSTEM alteram.")
