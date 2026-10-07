"""The permission matrix under the agent's home (docs/adr/0022)."""

import getpass
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from regista_agent import layout
from regista_agent.config import AgentSettings

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only (ACLs)")
ROBOT = r"NT SERVICE\RegistaRobotTest"


def _entry(rel: str) -> layout.Entry:
    return next(e for e in layout.MATRIX if e.rel == rel)


# --- the table itself (any system) -------------------------------------------------------------


@pytest.mark.parametrize(
    "rel", ["keys", "keys/machine.key", "keys/identity.json", "agent.toml", "PAUSED"]
)
def test_the_robot_reaches_nothing_of_the_agents_secrets_and_settings(rel: str) -> None:
    assert _entry(rel).robot == "-"


@pytest.mark.parametrize("rel", ["logs", "packages", "uv-cache", "runs", ""])
def test_the_robot_never_reads_or_writes_the_agents_working_folders(rel: str) -> None:
    assert _entry(rel).robot in ("-", "T")  # at most: be able to reach a path beneath it


@pytest.mark.parametrize("rel", ["python", "browsers"])
def test_the_runtime_is_read_only_for_both(rel: str) -> None:
    assert (_entry(rel).agent, _entry(rel).robot) == ("R", "R")


@pytest.mark.parametrize("rel", ["keys", "keys/machine.key", "agent.toml", "PAUSED", "python"])
def test_the_agent_cannot_change_its_own_key_settings_kill_switch_or_runtime(rel: str) -> None:
    assert _entry(rel).agent == "R"


def test_no_entry_gives_the_robot_more_than_read() -> None:
    assert all(e.robot in ("-", "T", "R") for e in layout.MATRIX)


def test_the_robot_gets_a_traverse_only_entry_that_does_not_inherit() -> None:
    sddl = (
        layout.sddl_for(_entry("runs"), "S-1-5-80-1", "S-1-5-80-2")
        if sys.platform == "win32"
        else ""
    )
    if sddl:
        assert "(A;;0x1000a0;;;S-1-5-80-2)" in sddl  # no OICI: only this folder, no listing


# --- the real ACLs (Windows) -------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    for name in list(os.environ):
        if name.startswith("REGISTA_"):
            monkeypatch.delenv(name)
    folder = tmp_path / "Regista"
    monkeypatch.setenv("REGISTA_HOME", str(folder))
    yield folder
    if sys.platform == "win32" and folder.exists():
        from regista_agent import _windows

        me = _windows.resolve_sid(f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}")
        for entry in layout.MATRIX:
            path = folder / entry.rel if entry.rel else folder
            if entry.kind == "dir" and path.exists():
                _windows.apply_dacl(path, f"D:(A;OICI;FA;;;{me})")


def _sids() -> tuple[str, str]:
    from regista_agent import _windows

    me = _windows.resolve_sid(f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}")
    return me, _windows.resolve_sid(ROBOT)


def _settings() -> AgentSettings:
    return AgentSettings(environment="dev")


@windows_only
def test_apply_writes_a_matrix_that_check_reads_back_as_correct(home: Path) -> None:
    agent_sid, robot_sid = _sids()
    layout.apply(_settings(), agent_sid, robot_sid)
    assert layout.check(_settings(), agent_sid, robot_sid) == []
    layout.apply(_settings(), agent_sid, robot_sid)  # idempotent
    assert layout.check(_settings(), agent_sid, robot_sid) == []


@windows_only
def test_apply_removes_what_an_older_version_left(home: Path) -> None:
    agent_sid, robot_sid = _sids()
    settings = _settings()
    (home / "envs" / "abc").mkdir(parents=True)
    (home / "envs" / "abc" / "evil.pth").write_text("x")
    (home / "uv-cache" / "wheels").mkdir(parents=True)
    (home / "uv-cache" / "stray.txt").write_text("x")
    removed = layout.apply(settings, agent_sid, robot_sid)
    assert not (home / "envs").exists()
    assert (home / "uv-cache").is_dir() and not any((home / "uv-cache").iterdir())
    assert len(removed) == 2


@windows_only
@pytest.mark.parametrize(
    ("rel", "extra", "expect"),
    [
        ("packages", "(A;OICI;FA;;;BU)", "fora da lista"),  # local users
        ("keys", "(A;OICI;FR;;;{robot})", "acesso demais"),  # the robot reads the key
        ("python", "(A;OICI;FA;;;{robot})", "acesso demais"),  # the robot rewrites the runtime
        ("uv-cache", "(A;OICI;FRFX;;;WD)", "fora da lista"),  # everyone
        ("logs", "(A;OICI;FRFX;;;AU)", "fora da lista"),  # authenticated users
    ],
)
def test_check_names_every_way_the_matrix_can_be_opened_up(
    home: Path, rel: str, extra: str, expect: str
) -> None:
    from regista_agent import _windows

    agent_sid, robot_sid = _sids()
    layout.apply(_settings(), agent_sid, robot_sid)
    folder = home / rel
    current = _windows.read_dacl(folder)
    sddl = "D:P" + "".join(f"({a.kind};{a.flags};{a.rights};;;{a.sid})" for a in current.aces)
    _windows.apply_dacl(folder, sddl + extra.format(robot=robot_sid))
    problems = layout.check(_settings(), agent_sid, robot_sid)
    assert any(expect in p and rel in p for p in problems), problems


@windows_only
def test_check_notices_a_folder_that_inherits_from_above(home: Path) -> None:
    from regista_agent import _windows

    agent_sid, robot_sid = _sids()
    layout.apply(_settings(), agent_sid, robot_sid)
    me = agent_sid
    _windows.apply_dacl(home / "packages", f"D:(A;OICI;FA;;;{me})")  # no P: inheritance on
    assert any("herda" in p for p in layout.check(_settings(), agent_sid, robot_sid))


@windows_only
def test_check_notices_when_the_agent_loses_what_it_needs(home: Path) -> None:
    from regista_agent import _windows

    agent_sid, robot_sid = _sids()
    layout.apply(_settings(), agent_sid, robot_sid)
    # The runtime without any entry for the robot: it could not run its Python.
    _windows.apply_dacl(
        home / "python",
        f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FRFX;;;{agent_sid})",
    )
    assert any(
        "não tem o acesso necessário" in p for p in layout.check(_settings(), agent_sid, robot_sid)
    )
