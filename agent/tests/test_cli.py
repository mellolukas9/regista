"""The command line: what the person at the console sees, and the exit code scripts rely on."""

import uuid
from pathlib import Path

import pytest

from regista_agent import cli, diagnose, loop, sysinfo
from regista_agent import enroll as enroll_module
from regista_agent.config import AgentSettings
from regista_agent.diagnose import Check
from regista_agent.errors import EnrollmentRefused, MachineRevoked

MACHINE_ID = uuid.uuid4()


def _exit(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)
    code = stopped.value.code
    assert isinstance(code, int)
    return code


def test_usage_mistakes_exit_with_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit([]) == 2  # no command
    assert _exit(["enroll"]) == 2  # missing --url and --key
    assert _exit(["run", "--mode", "root"]) == 2
    assert _exit(["dance"]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_version_prints_the_agent_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit(["--version"]) == 0
    assert capsys.readouterr().out.strip() == sysinfo.agent_version()


def test_run_before_enrolling_explains_what_to_do(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _exit(["run"]) == 1
    err = capsys.readouterr().err
    assert "Erro:" in err and "regista-agent enroll" in err


def test_a_revoked_machine_exits_with_3_and_says_how_to_come_back(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def revoked(settings: AgentSettings, **kwargs: object) -> None:
        raise MachineRevoked

    monkeypatch.setattr(loop, "run", revoked)
    assert _exit(["run"]) == 3
    assert "Esta máquina foi revogada no Regista. Para usar de novo, cadastre outra vez." in (
        capsys.readouterr().err
    )


def test_ctrl_c_ends_the_agent_cleanly(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(settings: AgentSettings, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(loop, "run", interrupted)
    assert _exit(["run"]) == 0
    assert "Agente encerrado." in capsys.readouterr().out


def test_run_passes_the_mode_to_confirm(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(settings: AgentSettings, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(loop, "run", fake_run)
    assert _exit(["run", "--mode", "session"]) == 0
    assert seen["expected_mode"] == "session"


def test_enroll_reports_what_it_did(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: dict[str, object] = {}

    def fake_enroll(settings: AgentSettings, **kwargs: object) -> enroll_module.EnrollResult:
        calls.update(kwargs)
        return enroll_module.EnrollResult(
            machine_id=MACHINE_ID,
            mode="service",
            heartbeat_seconds=30,
            config_path=home / "agent.toml",
            agent_account="tester",
        )

    monkeypatch.setattr(enroll_module, "enroll", fake_enroll)
    code = _exit(
        [
            "enroll",
            "--url",
            "https://regista.exemplo.com.br",
            "--key",
            "rgk_x",
            "--agent-account",
            "tester",
            "--force",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert str(MACHINE_ID) in out and "modo service" in out and "tester" in out
    assert calls == {
        "url": "https://regista.exemplo.com.br",
        "key": "rgk_x",
        "agent_account": "tester",
        "robot_account": None,
        "force": True,
    }


def test_a_refused_enrollment_exits_with_1_and_the_reason(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(settings: AgentSettings, **kwargs: object) -> None:
        raise EnrollmentRefused("A chave foi recusada.")

    monkeypatch.setattr(enroll_module, "enroll", refused)
    assert _exit(["enroll", "--url", "https://s.example", "--key", "rgk_x"]) == 1
    assert "Erro: A chave foi recusada." in capsys.readouterr().err


def test_diagnose_prints_the_report_and_exits_by_the_result(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        diagnose,
        "run_checks",
        lambda settings: [Check("A", "ok", "bem"), Check("B", "erro", "mal")],
    )
    assert _exit(["diagnose"]) == 1
    out = capsys.readouterr().out
    assert "[OK   ] A: bem" in out and "[ERRO ] B: mal" in out

    monkeypatch.setattr(diagnose, "run_checks", lambda settings: [Check("A", "ok", "bem")])
    assert _exit(["diagnose"]) == 0
    assert "Tudo certo." in capsys.readouterr().out
