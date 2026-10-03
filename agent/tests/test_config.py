"""Configuration: the TOML file in the agent's home, overridden by REGISTA_* variables."""

import sys
import uuid
from pathlib import Path

import pytest

from regista_agent.config import AgentSettings, default_home, save_identity


def test_everything_is_empty_before_enrolling(home: Path) -> None:
    settings = AgentSettings()
    assert settings.server_url is None and settings.machine_id is None
    assert not settings.enrolled
    assert settings.home == home
    assert settings.key_path == home / "keys" / "machine.key"
    assert settings.log_path == home / "logs" / "agent.log"


def test_the_file_is_read_and_the_environment_wins(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_id = uuid.uuid4()
    home.mkdir()
    (home / "agent.toml").write_text(
        f'server_url = "https://da-config.example"\nmachine_id = "{machine_id}"\n'
        'mode = "session"\nproxy = "http://proxy.da-config:3128"\nlog_level = "DEBUG"\n',
        encoding="utf-8",
    )
    settings = AgentSettings()
    assert settings.enrolled
    assert (settings.server_url, settings.machine_id, settings.mode) == (
        "https://da-config.example",
        machine_id,
        "session",
    )
    assert settings.proxy == "http://proxy.da-config:3128" and settings.log_level == "DEBUG"

    monkeypatch.setenv("REGISTA_SERVER_URL", "https://do-ambiente.example")
    monkeypatch.setenv("REGISTA_PROXY", "http://proxy.do-ambiente:8080")
    monkeypatch.setenv("REGISTA_CA_BUNDLE", str(home / "ca.pem"))
    monkeypatch.setenv("REGISTA_LOG_LEVEL", "WARNING")
    overridden = AgentSettings()
    assert overridden.server_url == "https://do-ambiente.example"
    assert overridden.proxy == "http://proxy.do-ambiente:8080"
    assert overridden.ca_bundle == home / "ca.pem" and overridden.log_level == "WARNING"
    assert overridden.machine_id == machine_id  # not overridden: it still comes from the file


def test_an_invalid_mode_in_the_file_is_refused(home: Path) -> None:
    home.mkdir()
    (home / "agent.toml").write_text('mode = "root"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="mode"):
        AgentSettings()


def test_the_home_can_be_moved_and_has_a_system_default(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert default_home() == home
    monkeypatch.delenv("REGISTA_HOME")
    if sys.platform == "win32":
        monkeypatch.setenv("ProgramData", r"C:\Dados")
        assert default_home() == Path(r"C:\Dados") / "Regista"
    else:
        assert default_home() == Path("/etc/regista")


def test_saving_the_identity_keeps_the_other_settings(home: Path) -> None:
    home.mkdir()
    (home / "agent.toml").write_text(
        'proxy = "http://p:1"\nlog_level = "DEBUG"\n', encoding="utf-8"
    )
    machine_id = uuid.uuid4()
    path = save_identity(
        home,
        server_url="https://s.example",
        machine_id=machine_id,
        mode="service",
        agent_account=r"NT SERVICE\RegistaAgent",
    )
    settings = AgentSettings()
    assert path == home / "agent.toml"
    assert settings.proxy == "http://p:1" and settings.log_level == "DEBUG"
    assert settings.machine_id == machine_id and settings.mode == "service"
    assert settings.agent_account == r"NT SERVICE\RegistaAgent"
    assert not (home / "agent.toml.new").exists()  # swapped in, not left behind
