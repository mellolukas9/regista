"""The minimal installer: what it asks Windows for, and what it refuses (docs/adr/0022).

The calls to `sc.exe` and `schtasks.exe` are recorded, not run; the real services and accounts are
exercised by the Windows job of the CI (`test_windows_host.py`)."""

import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

import pytest

from regista_agent import service
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
PYTHON = Path(r"C:\Program Files\Regista\app\Scripts\python.exe")


class Recorder:
    """Stands in for sc.exe and schtasks.exe; knows which services exist."""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.services = set(existing or ())
        self.xml_seen: list[str] = []

    def __call__(self, args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        call = list(args)
        self.calls.append(call)
        if call[0] == "sc.exe" and call[1] == "query":
            missing = call[2] not in self.services
            return subprocess.CompletedProcess(
                call, 1060 if missing else 0, "STATE : 4 RUNNING", ""
            )
        if call[0] == "sc.exe" and call[1] == "create":
            self.services.add(call[2])
        if call[0] == "schtasks.exe" and "/XML" in call:
            self.xml_seen.append(Path(call[call.index("/XML") + 1]).read_text(encoding="utf-16"))
        return subprocess.CompletedProcess(call, 0, "", "")

    def of(self, tool: str, verb: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == tool and c[1].lower() == verb.lower()]


@pytest.fixture
def settings(home: Path) -> AgentSettings:
    return AgentSettings(environment="dev")


# --- what the services look like ---------------------------------------------------------------


def test_the_binary_path_quotes_the_interpreter() -> None:
    path = service.binary_path(PYTHON, "service run agent")
    assert (
        path
        == r'"C:\Program Files\Regista\app\Scripts\python.exe" -m regista_agent service run agent'
    )


def test_service_mode_has_the_agent_and_the_host_each_with_its_own_virtual_account() -> None:
    both = service.specs("service")
    assert [s.name for s in both] == ["RegistaAgent", "RegistaRobot"]
    assert [s.account for s in both] == [r"NT SERVICE\RegistaAgent", r"NT SERVICE\RegistaRobot"]
    assert len({s.account for s in both}) == 2, "never the same identity"


def test_session_mode_has_only_the_agent_service() -> None:
    assert [s.name for s in service.specs("session")] == ["RegistaAgent"]


def test_the_logon_task_starts_for_one_user_only_without_a_password() -> None:
    xml = service.task_xml(PYTHON, r"PC\usuario-dedicado")
    root = ET.fromstring(xml.split("?>", 1)[1])  # noqa: S314  (our own XML)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    trigger = root.find("t:Triggers/t:LogonTrigger/t:UserId", ns)
    assert trigger is not None and trigger.text == r"PC\usuario-dedicado"
    principal = root.find("t:Principals/t:Principal", ns)
    assert principal is not None
    assert principal.findtext("t:LogonType", namespaces=ns) == "InteractiveToken"
    assert principal.findtext("t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert "password" not in xml.lower()
    assert "-m regista_agent host --mode session" in xml
    assert "&" not in xml.replace("&amp;", "")  # escaped


def test_the_task_xml_escapes_what_it_is_given() -> None:
    xml = service.task_xml(Path(r"C:\a & b\python.exe"), "PC\\<x>")
    ET.fromstring(xml.split("?>", 1)[1])  # noqa: S314  (still well formed)


# --- installing --------------------------------------------------------------------------------


@windows_only
def test_install_creates_both_services_with_failure_actions(settings: AgentSettings) -> None:
    run = Recorder()
    said: list[str] = []
    service.install(
        settings, mode="service", robot_account=None, python=PYTHON, allow_insecure_path=True,
        say=said.append, run=run,
    )  # fmt: skip
    created = run.of("sc.exe", "create")
    assert [c[2] for c in created] == ["RegistaAgent", "RegistaRobot"]
    for call in created:
        assert call[call.index("binPath=") + 1].startswith(f'"{PYTHON.resolve()}"')
        assert call[call.index("start=") + 1] == "auto"
    accounts = [c[c.index("obj=") + 1] for c in created]
    assert accounts == [r"NT SERVICE\RegistaAgent", r"NT SERVICE\RegistaRobot"]
    assert len(run.of("sc.exe", "failure")) == 2 and len(run.of("sc.exe", "failureflag")) == 2
    assert not run.of("schtasks.exe", "/Create"), "no logon task in service mode"
    assert any("AVISO" in line for line in said), "a development path is said out loud"


@windows_only
def test_install_twice_updates_instead_of_creating_again(settings: AgentSettings) -> None:
    run = Recorder()
    for _ in range(2):
        service.install(
            settings, mode="service", robot_account=None, python=PYTHON, allow_insecure_path=True,
            say=lambda _t: None, run=run,
        )  # fmt: skip
    assert len(run.of("sc.exe", "create")) == 2
    assert len(run.of("sc.exe", "config")) == 2


@windows_only
def test_session_mode_installs_the_agent_and_a_logon_task_for_the_dedicated_user(
    settings: AgentSettings,
) -> None:
    run = Recorder()
    with pytest.raises(AgentError, match="--robot-account"):
        service.install(
            settings, mode="session", robot_account=None, python=PYTHON, allow_insecure_path=True,
            say=lambda _t: None, run=run,
        )  # fmt: skip
    service.install(
        settings, mode="session", robot_account=r"PC\dedicado", python=PYTHON,
        allow_insecure_path=True, say=lambda _t: None, run=run,
    )  # fmt: skip
    assert [c[2] for c in run.of("sc.exe", "create")] == ["RegistaAgent"], "no host service"
    task = run.of("schtasks.exe", "/Create")[0]
    assert "/XML" in task and "/F" in task
    assert "/RP" not in task and "/RU" not in task, "no password and no run-as: the XML says it"
    assert r"PC\dedicado" in run.xml_seen[0]


@windows_only
def test_install_refuses_a_location_that_others_can_change(
    settings: AgentSettings, tmp_path: Path
) -> None:
    run = Recorder()
    with pytest.raises(AgentError, match="--allow-insecure-path"):
        service.install(
            settings, mode="service", robot_account=None, python=Path(sys.executable),
            say=lambda _t: None, run=run,
        )  # fmt: skip
    assert not run.of("sc.exe", "create"), "nothing was created"


@windows_only
def test_uninstall_removes_the_host_first_then_the_agent_and_the_task(
    settings: AgentSettings,
) -> None:
    run = Recorder({"RegistaAgent", "RegistaRobot"})
    service.uninstall(settings, say=lambda _t: None, run=run)
    deleted = [c[2] for c in run.of("sc.exe", "delete")]
    assert deleted == ["RegistaRobot", "RegistaAgent"]
    assert run.of("schtasks.exe", "/Delete")


@windows_only
def test_uninstall_with_nothing_installed_is_not_an_error(settings: AgentSettings) -> None:
    run = Recorder()
    service.uninstall(settings, say=lambda _t: None, run=run)
    assert not run.of("sc.exe", "delete")


# --- program files -----------------------------------------------------------------------------


@windows_only
def test_a_folder_the_user_owns_is_flagged_and_the_windows_folder_is_not(tmp_path: Path) -> None:
    from regista_agent import _windows

    assert _windows.writable_by_others(tmp_path), "a folder in the user's profile"
    assert _windows.writable_by_others(Path(r"C:\Windows\System32")) == []
