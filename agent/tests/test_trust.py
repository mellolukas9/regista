"""Which keys the agent trusts, and why production cannot be talked into trusting another."""

import base64
import json
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent import loop, trust
from regista_agent.config import AgentSettings, save_identity
from regista_agent.errors import AgentError
from regista_pkg import key_id_of

from .jobs_support import FakeApi
from .test_jobs_loop import MACHINE_ID, ScriptedSession


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


def _key_file(tmp_path: Path) -> tuple[Path, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    kid = key_id_of(raw)
    path = tmp_path / "dev-keys.json"
    path.write_text(json.dumps({kid: base64.b64encode(raw).decode()}), encoding="utf-8")
    return path, kid


def test_without_the_override_only_the_compiled_in_keys_count(home: Path) -> None:
    keys = trust.trusted_keys("prod", {})
    assert keys == trust.trusted_keys("dev", {})  # the same list in both


def test_in_development_the_override_adds_a_key(home: Path, tmp_path: Path) -> None:
    path, kid = _key_file(tmp_path)
    keys = trust.trusted_keys("dev", {trust.ENV_DEV_TRUSTED_KEYS: str(path)})
    assert kid in keys


def test_in_production_the_override_is_refused_not_ignored(home: Path, tmp_path: Path) -> None:
    path, _ = _key_file(tmp_path)
    with pytest.raises(AgentError, match="REGISTA_ENVIRONMENT=dev"):
        trust.trusted_keys("prod", {trust.ENV_DEV_TRUSTED_KEYS: str(path)})


def test_the_agent_does_not_start_in_production_with_the_override_set(
    identity: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_acl: None
) -> None:
    path, _ = _key_file(tmp_path)
    monkeypatch.setenv(trust.ENV_DEV_TRUSTED_KEYS, str(path))
    session = ScriptedSession()
    with pytest.raises(AgentError, match="REGISTA_ENVIRONMENT=dev"):
        loop.run(AgentSettings(), stop=threading.Event(), session=session, jobs=FakeApi())
    assert session.sent == [], "it refused before talking to anyone"


def test_the_settings_file_cannot_carry_the_override(home: Path, tmp_path: Path) -> None:
    """The override is read from the process environment only. A line in agent.toml is not a
    setting and changes nothing."""
    path, kid = _key_file(tmp_path)
    home.mkdir(parents=True)
    (home / "agent.toml").write_text(
        f'environment = "dev"\ndev_trusted_keys = "{path.as_posix()}"\n', encoding="utf-8"
    )
    settings = AgentSettings()
    assert settings.environment == "dev"
    assert kid not in trust.trusted_keys(settings.environment, {})


def test_a_bad_override_file_stops_the_agent_with_a_clear_message(
    home: Path, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(AgentError, match=trust.ENV_DEV_TRUSTED_KEYS):
        trust.trusted_keys("dev", {trust.ENV_DEV_TRUSTED_KEYS: str(broken)})
    with pytest.raises(AgentError, match=trust.ENV_DEV_TRUSTED_KEYS):
        trust.trusted_keys("dev", {trust.ENV_DEV_TRUSTED_KEYS: str(tmp_path / "missing.json")})
