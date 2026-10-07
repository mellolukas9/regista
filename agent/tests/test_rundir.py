"""The folder of one run and the files a robot leaves in it (docs/adr/0022)."""

import getpass
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from regista_agent import layout, rundir, safefs
from regista_agent.config import AgentSettings
from regista_agent.jobs import JobExecutor, JobState
from regista_agent.launcher import DirectLauncher

from .jobs_support import FakeApi, job

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only (ACLs)")


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AgentSettings]:
    for name in list(os.environ):
        if name.startswith("REGISTA_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("REGISTA_HOME", str(tmp_path / "Regista"))
    yield AgentSettings(environment="dev")


def _link_dir(link: Path, target: Path) -> None:
    """A junction on Windows (no privilege needed), a symlink elsewhere."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


# --- the folder --------------------------------------------------------------------------------


def test_a_run_folder_has_its_parts_and_a_name_of_its_own(settings: AgentSettings) -> None:
    first, second = rundir.create(settings), rundir.create(settings)
    assert first.root != second.root and first.root.parent == settings.runs_dir
    assert len(first.name) == 12, "short: Windows paths are limited to 260 characters"
    for part in (first.build, first.package, first.venv, first.tmp, first.artifacts):
        assert part.is_dir()
    assert first.cancel_file.parent == first.root


def test_removing_a_run_folder_removes_everything_in_it(settings: AgentSettings) -> None:
    run = rundir.create(settings)
    (run.tmp / "deep" / "er").mkdir(parents=True)
    (run.tmp / "deep" / "er" / "readonly.txt").write_text("x")
    os.chmod(run.tmp / "deep" / "er" / "readonly.txt", 0o444)
    rundir.remove(run)
    assert not run.root.exists()


def test_removing_a_run_folder_does_not_follow_a_link_into_the_agents_files(
    settings: AgentSettings, tmp_path: Path
) -> None:
    secret = tmp_path / "keys"
    secret.mkdir()
    (secret / "machine.key").write_text("private")
    run = rundir.create(settings)
    _link_dir(run.artifacts / "link", secret)
    rundir.remove(run)
    assert not run.root.exists()
    assert (secret / "machine.key").read_text() == "private", "the target was left alone"


def test_the_sweep_at_startup_removes_what_a_crash_left(settings: AgentSettings) -> None:
    leftover = rundir.create(settings)
    (leftover.tmp / "x").write_text("x")
    rundir.sweep(settings)
    assert not leftover.root.exists() and settings.runs_dir.is_dir()


# --- files the robot left behind ---------------------------------------------------------------


def test_only_ordinary_files_are_read_back(tmp_path: Path) -> None:
    folder = tmp_path / "artifacts"
    folder.mkdir()
    (folder / "ok.png").write_bytes(b"png")
    (folder / "big.png").write_bytes(b"x" * 100)
    assert safefs.read_regular_file(folder / "ok.png", 10) == b"png"
    assert safefs.read_regular_file(folder / "big.png", 10) is None, "too big"
    assert safefs.read_regular_file(folder / "missing.png", 10) is None
    assert safefs.read_regular_file(folder, 10) is None, "a folder is not a file"


def test_a_link_to_the_agents_key_is_not_read_as_a_screenshot(tmp_path: Path) -> None:
    secret = tmp_path / "keys"
    secret.mkdir()
    (secret / "machine.key").write_bytes(b"private-key-bytes")
    folder = tmp_path / "artifacts"
    folder.mkdir()
    _link_dir(folder / "shot.png", secret)  # a link where a file is expected
    assert safefs.regular_files(folder) == [], "a link is not an ordinary file"
    assert safefs.read_regular_file(folder / "shot.png", 1000) is None
    if sys.platform != "win32":
        (folder / "key.png").symlink_to(secret / "machine.key")
        assert safefs.read_regular_file(folder / "key.png", 1000) is None
        assert safefs.regular_files(folder) == []


def test_no_link_is_uploaded_as_a_screenshot_and_ordinary_ones_are(
    settings: AgentSettings, tmp_path: Path
) -> None:
    secret = tmp_path / "keys"
    secret.mkdir()
    run = rundir.create(settings)
    (run.artifacts / "real.png").write_bytes(b"\x89PNG-real")
    _link_dir(run.artifacts / "evil.png", secret)
    api = FakeApi()
    executor = JobExecutor(
        settings, api, JobState(), threading.Event(), robot_launcher=DirectLauncher()
    )
    executor._upload_screenshots(job(), run.artifacts)
    assert [u[2] for u in api.uploads] == [b"\x89PNG-real"]
    rundir.remove(run)


# --- the permissions (Windows) -----------------------------------------------------------------


@pytest.fixture
def locked(settings: AgentSettings) -> Iterator[tuple[AgentSettings, str, str]]:
    from regista_agent import _windows

    me = _windows.resolve_sid(f"{os.environ['COMPUTERNAME']}\\{getpass.getuser()}")
    robot = _windows.resolve_sid(r"NT SERVICE\RegistaRobotTest")
    yield settings, me, robot


@windows_only
def test_each_part_of_a_run_folder_is_born_with_the_right_permissions(
    locked: tuple[AgentSettings, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    from regista_agent import _windows

    settings, agent_sid, robot_sid = locked
    monkeypatch.setattr(layout, "lock_applies", lambda s: True)
    run = rundir.create(settings, agent_sid, robot_sid)
    levels = {}
    for name, path in (
        ("root", run.root),
        ("build", run.build),
        ("package", run.package),
        ("venv", run.venv),
        ("tmp", run.tmp),
        ("artifacts", run.artifacts),
    ):
        dacl = _windows.read_dacl(path)
        assert dacl.protected, f"{name} inherits from above"
        allowed = {a.sid for a in dacl.aces if a.kind == "A"}
        assert allowed <= {
            _windows.SYSTEM_SID,
            _windows.ADMINISTRATORS_SID,
            agent_sid,
            robot_sid,
            "S-1-3-4",  # owner rights: read the ACL only
        }, f"{name}: someone else has access"
        levels[name] = max(
            (a.level for a in dacl.aces if a.sid == robot_sid and a.kind == "A"),
            key=_windows.LEVELS.index,
            default="-",
        )
    assert levels["build"] == "-", "the robot never sees what the environment is made from"
    assert levels["package"] == "R" and levels["venv"] == "R", "it reads and runs, never writes"
    assert levels["tmp"] == "M" and levels["artifacts"] == "M"
    assert levels["root"] in ("T", "R")
    # What the robot writes cannot be used to change the ACL (owner rights are cut to reading it).
    owner = next(a for a in _windows.read_dacl(run.tmp).aces if a.sid == "S-1-3-4")
    assert not owner.can_write
    # And what is created inside inherits that, so the agent can always delete it.
    child = run.tmp / "made-by-robot.txt"
    child.write_text("x")
    child_acl = _windows.read_dacl(child)
    assert any(a.sid == agent_sid and a.level == "F" for a in child_acl.aces)
    rundir.remove(run)
    # The agent's own SID is the test user's, so removal worked; nothing is left behind.
    assert not run.root.exists()
