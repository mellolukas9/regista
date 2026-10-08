"""The run loop in development mode, and the guard that keeps that mode out of production (M3)."""

import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from regista_agent import loop
from regista_agent.config import AgentSettings, save_identity
from regista_agent.errors import AgentError, MachineRevoked
from regista_agent.launcher import DirectLauncher
from regista_agent.transport import HeartbeatInfo

from .jobs_support import FIXTURE_BOTS, FakeApi, job

MACHINE_ID = uuid.uuid4()


@pytest.fixture
def identity(home: Path) -> Path:
    save_identity(
        home,
        server_url="https://regista.exemplo.com.br",
        machine_id=MACHINE_ID,
        mode="service",
        agent_account="tester",
    )
    return home


class ScriptedSession:
    """Plays the heartbeat side and records what the agent said in it."""

    def __init__(self) -> None:
        self.sent: list[str | None] = []
        self.cancel_current = False
        self.revoke_after: int | None = None

    def heartbeat(
        self,
        *,
        agent_version: str,
        os_info: dict[str, str],
        interactive_session: bool,
        current_job_id: str | None = None,
        paused: bool = False,
    ) -> HeartbeatInfo:
        self.sent.append(current_job_id)
        if self.revoke_after is not None and len(self.sent) > self.revoke_after:
            raise MachineRevoked
        cancellations = [current_job_id] if (self.cancel_current and current_job_id) else []
        return HeartbeatInfo(heartbeat_seconds=1, mode="service", cancellations=cancellations)


def _dev_env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("REGISTA_ENVIRONMENT", "dev")
    monkeypatch.setenv("REGISTA_DEV_UNSIGNED", "1")
    monkeypatch.setenv("REGISTA_DEV_BOTS_DIR", str(FIXTURE_BOTS))
    monkeypatch.setenv("REGISTA_CANCEL_GRACE_SECONDS", "1")
    monkeypatch.setenv("REGISTA_POLL_WAIT_SECONDS", "0")
    monkeypatch.setenv("REGISTA_JOB_PRIORITY", "normal")
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def _run_in_thread(
    settings: AgentSettings, session: Any, api: FakeApi, stop: threading.Event
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def go() -> None:
        try:
            loop.run(
                settings, stop=stop, session=session, jobs=api, robot_launcher=DirectLauncher()
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=go)
    thread.start()
    return thread, errors


def _wait(check: Any, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.1)


# --- the guard ---------------------------------------------------------------------------------


def test_the_unsigned_mode_is_refused_in_production(
    identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REGISTA_DEV_UNSIGNED", "1")
    monkeypatch.setenv("REGISTA_DEV_BOTS_DIR", str(FIXTURE_BOTS))
    for environment in (None, "prod", "production"):  # unset is production too
        if environment is None:
            monkeypatch.delenv("REGISTA_ENVIRONMENT", raising=False)
        else:
            monkeypatch.setenv("REGISTA_ENVIRONMENT", environment)
        try:
            settings = AgentSettings()
        except Exception:  # "production" is not a value: refused even earlier
            assert environment == "production"
            continue
        session, api = ScriptedSession(), FakeApi()
        with pytest.raises(AgentError, match="REGISTA_ENVIRONMENT=dev"):
            loop.run(settings, stop=threading.Event(), session=session, jobs=api)
        assert session.sent == [], "nothing was sent: it refused before talking to anyone"


def test_the_unsigned_mode_needs_a_real_bots_folder(
    identity: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _dev_env(monkeypatch)
    monkeypatch.delenv("REGISTA_DEV_BOTS_DIR")
    with pytest.raises(AgentError, match="REGISTA_DEV_BOTS_DIR"):
        AgentSettings().check_dev_unsigned()
    monkeypatch.setenv("REGISTA_DEV_BOTS_DIR", str(tmp_path / "missing"))
    with pytest.raises(AgentError, match="REGISTA_DEV_BOTS_DIR"):
        AgentSettings().check_dev_unsigned()


def test_without_the_dev_flag_a_run_with_no_version_never_runs_a_folder_robot(
    identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Since M4 the agent always takes runs, but only a version runs. A run that names none (a
    server that sends one in production, or a dev agent without the flag) is refused as not found,
    and no robot is started, whatever folder of robots sits on the machine."""
    monkeypatch.setenv("REGISTA_ENVIRONMENT", "dev")  # dev alone is not enough
    monkeypatch.setenv("REGISTA_DEV_DIRECT_ROBOT", "1")  # (the host has its own tests)
    monkeypatch.setenv("REGISTA_DEV_BOTS_DIR", str(FIXTURE_BOTS))
    settings = AgentSettings()
    assert settings.dev_unsigned is False
    stop = threading.Event()

    class StoppingApi(FakeApi):
        def fail(
            self, job_id: str, error_code: str, message: str, reason: str | None = None
        ) -> None:
            super().fail(job_id, error_code, message, reason)
            stop.set()

    api = StoppingApi()
    api.queue = [job("ok")]

    class Beats:
        def heartbeat(self, **_: Any) -> HeartbeatInfo:
            return HeartbeatInfo(heartbeat_seconds=1, mode="service", cancellations=[])

    loop.run(settings, stop=stop, session=Beats(), jobs=api, robot_launcher=DirectLauncher())
    assert [f[1] for f in api.failed] == ["robot_not_found"]
    assert api.completed == [] and not api.lines, "no robot was started"


def test_the_defaults_are_the_safe_ones(identity: Path) -> None:
    settings = AgentSettings()
    assert settings.environment == "prod" and settings.dev_unsigned is False
    settings.check_dev_unsigned()  # nothing to refuse


# --- the loop in development mode --------------------------------------------------------------


def test_a_run_goes_through_and_the_heartbeat_says_what_it_is_busy_with(
    identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dev_env(monkeypatch)
    session, api, stop = ScriptedSession(), FakeApi(), threading.Event()
    api.queue = [job("ok")]
    thread, errors = _run_in_thread(AgentSettings(), session, api, stop)
    _wait(lambda: api.completed)
    stop.set()
    thread.join(20)
    assert not errors and not thread.is_alive()
    assert api.completed == [job().job_id]


def test_a_cancellation_in_the_heartbeat_stops_the_robot(
    identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dev_env(monkeypatch)
    session, api, stop = ScriptedSession(), FakeApi(), threading.Event()
    session.cancel_current = True
    api.queue = [job("wait")]
    thread, errors = _run_in_thread(AgentSettings(), session, api, stop)
    _wait(lambda: api.failed)
    stop.set()
    thread.join(20)
    assert not errors
    assert api.failed == [(job().job_id, "cancelled", "")]
    assert job().job_id in session.sent, "the heartbeat named the run it was busy with"


def test_revocation_seen_by_the_heartbeat_kills_the_robot_and_stops_the_agent(
    identity: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dev_env(monkeypatch)
    session, api, stop = ScriptedSession(), FakeApi(), threading.Event()
    session.revoke_after = 2
    api.queue = [job("stubborn")]
    thread, errors = _run_in_thread(AgentSettings(), session, api, stop)
    thread.join(40)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MachineRevoked)
    assert api.completed == []
