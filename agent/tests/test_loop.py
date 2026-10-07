"""`regista-agent run`: the heartbeat loop, the modes and the local log."""

import logging
import threading
import uuid
from pathlib import Path

import pytest

from regista_agent import logs, loop, modes
from regista_agent.config import AgentSettings, save_identity
from regista_agent.errors import (
    AgentError,
    MachineRevoked,
    NotEnrolled,
    ServerUnavailable,
)
from regista_agent.launcher import DirectLauncher
from regista_agent.modes import session as session_mode
from regista_agent.transport import HeartbeatInfo

MACHINE_ID = uuid.uuid4()


class FakeStop(threading.Event):
    """Never really waits: records how long the loop asked to wait, and stops after `beats`."""

    def __init__(self, beats: int) -> None:
        super().__init__()
        self.beats = beats
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0)
        if len(self.waits) >= self.beats:
            self.set()
        return self.is_set()


class FakeSession:
    def __init__(self, answers: list[HeartbeatInfo | Exception]) -> None:
        self.answers = answers
        self.sent: list[dict[str, object]] = []

    def heartbeat(
        self, *, agent_version: str, os_info: dict[str, str], interactive_session: bool
    ) -> HeartbeatInfo:
        self.sent.append({"v": agent_version, "os": os_info, "interactive": interactive_session})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _info(seconds: int = 30) -> HeartbeatInfo:
    return HeartbeatInfo(heartbeat_seconds=seconds, mode="service", cancellations=[])


@pytest.fixture
def enrolled(home: Path) -> AgentSettings:
    save_identity(
        home,
        server_url="https://regista.exemplo.com.br",
        machine_id=MACHINE_ID,
        mode="service",
        agent_account="tester",
    )
    return AgentSettings()


def test_the_loop_follows_the_interval_the_server_asks_for(enrolled: AgentSettings) -> None:
    stop = FakeStop(beats=3)
    session = FakeSession([_info(30), _info(10), _info(45)])
    loop.run(enrolled, stop=stop, session=session)
    assert stop.waits == [30, 10, 45]
    assert len(session.sent) == 3
    assert session.sent[0]["v"] and "system" in session.sent[0]["os"]  # type: ignore[operator]


def test_trouble_with_the_network_is_logged_and_the_next_beat_tries_again(
    enrolled: AgentSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="regista_agent")
    stop = FakeStop(beats=3)
    session = FakeSession([_info(20), ServerUnavailable("sem rede"), _info(20)])
    loop.run(enrolled, stop=stop, session=session)
    assert len(session.sent) == 3  # it kept going
    assert stop.waits == [20, 20, 20]  # and kept the last interval it was given
    assert "sem rede" in caplog.text


def test_a_revoked_machine_ends_the_loop_with_the_revoked_exit(enrolled: AgentSettings) -> None:
    session = FakeSession([_info(), MachineRevoked()])
    with pytest.raises(MachineRevoked) as stopped:
        loop.run(enrolled, stop=FakeStop(beats=10), session=session)
    assert stopped.value.exit_code == 3
    assert "Esta máquina foi revogada no Regista" in str(stopped.value)


def test_the_loop_stops_when_asked(enrolled: AgentSettings) -> None:
    stop = threading.Event()
    stop.set()
    session = FakeSession([])
    loop.run(enrolled, stop=stop, session=session)
    assert session.sent == []


def test_an_unenrolled_machine_cannot_run(home: Path) -> None:
    with pytest.raises(NotEnrolled, match="enroll"):
        loop.run(AgentSettings(), stop=FakeStop(beats=1), session=FakeSession([]))


def test_the_mode_comes_from_the_enrollment_and_the_flag_can_only_confirm_it(
    enrolled: AgentSettings,
) -> None:
    loop.run(
        enrolled, expected_mode="service", stop=FakeStop(beats=1), session=FakeSession([_info()])
    )
    with pytest.raises(AgentError, match="cadastrada no modo service, não session"):
        loop.run(enrolled, expected_mode="session", stop=FakeStop(beats=1), session=FakeSession([]))


# --- modes ------------------------------------------------------------------------------------


def test_service_mode_runs_anywhere() -> None:
    modes.preflight("service")


def test_session_mode_refuses_a_place_with_no_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_mode, "is_interactive_session", lambda: False)
    with pytest.raises(AgentError, match="sessão 0 do Windows"):
        modes.preflight("session")
    monkeypatch.setattr(session_mode, "is_interactive_session", lambda: True)
    modes.preflight("session")


def test_oneshot_is_prepared_but_not_available_and_unknown_modes_are_refused() -> None:
    with pytest.raises(AgentError, match="oneshot ainda não está disponível"):
        modes.preflight("oneshot")
    with pytest.raises(AgentError, match="Modo desconhecido"):
        modes.preflight("root")
    assert modes.MODES == ("service", "session", "oneshot")


def test_the_loop_checks_the_mode_before_it_starts(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_identity(
        home,
        server_url="https://s.example",
        machine_id=MACHINE_ID,
        mode="session",
        agent_account="tester",
    )
    monkeypatch.setattr(session_mode, "is_interactive_session", lambda: False)
    session = FakeSession([_info()])
    with pytest.raises(AgentError, match="sessão 0"):
        loop.run(
            AgentSettings(),
            stop=FakeStop(beats=1),
            session=session,
            robot_launcher=DirectLauncher(),  # the robot is the agent's child: it needs a desktop
        )
    assert session.sent == []  # it never said it was alive


def test_through_the_host_the_agent_needs_no_desktop_the_host_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_mode, "is_interactive_session", lambda: False)
    modes.preflight("session", through_host=True)  # the agent is a service in session 0
    with pytest.raises(AgentError, match="sessão 0"):
        session_mode.preflight()  # what the host runs in session mode


# --- the local log ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("enrolling with rgk_AbC-123_xyz now", "rgk_AbC-123_xyz"),
        ("token rga1.eyJ0eXAi.mac-value used", "eyJ0eXAi"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("sent nonce=QUJDREVGRw== and signature=c2ln", "QUJDREVGRw=="),
        ("key=segredo proof=outro", "segredo"),
    ],
)
def test_secrets_are_scrubbed_from_log_text(raw: str, must_not_contain: str) -> None:
    cleaned = logs.scrub(raw)
    assert must_not_contain not in cleaned
    assert "[redacted]" in cleaned


def test_the_log_file_is_written_scrubbed_and_rotates(home: Path) -> None:
    settings = AgentSettings()
    logs.configure(settings)
    log = logging.getLogger("regista_agent")
    log.info("enrolled with key rgk_SECRETKEYVALUE and token rga1.payload.mac")
    log.info("an ordinary message")
    for handler in log.handlers:
        handler.flush()

    text = settings.log_path.read_text("utf-8")
    assert "an ordinary message" in text
    assert "SECRETKEYVALUE" not in text and "payload.mac" not in text
    assert any("Rotating" in type(h).__name__ for h in log.handlers)


def test_a_log_folder_that_cannot_be_created_falls_back_to_the_console(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home.mkdir()
    (home / "logs").write_text("this is a file, not a folder")
    logs.configure(AgentSettings())
    assert "sem arquivo de log" in capsys.readouterr().err
    logging.getLogger("regista_agent").info("still works")  # no crash
