"""`regista-agent enroll`: a machine gets its identity, and only if the server accepts it."""

import base64
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from regista_agent import enroll as enroll_module
from regista_agent.config import AgentSettings
from regista_agent.enroll import SESSION_NEEDS_ACCOUNT, normalize_url
from regista_agent.errors import AgentError, EnrollmentRefused, ServerUnavailable
from regista_agent.keystore import KeyStore
from regista_agent.transport import HttpSession  # noqa: F401

from .support import KEY, MACHINE_ID, SERVER, EnrollServer, _public


@pytest.fixture
def settings(home: Path, no_acl: None) -> AgentSettings:
    return AgentSettings()


def _enroll(
    settings: AgentSettings, server: EnrollServer, *, account: str | None = "tester", **kw: object
) -> enroll_module.EnrollResult:
    return enroll_module.enroll(
        settings,
        url=SERVER + "/",
        key=KEY,
        agent_account=account,
        http=server.session(),
        **kw,  # type: ignore[arg-type]
    )


def test_enrolling_gives_the_machine_its_identity(settings: AgentSettings) -> None:
    server = EnrollServer(mode="session")
    result = _enroll(settings, server)

    assert (result.machine_id, result.mode, result.heartbeat_seconds) == (MACHINE_ID, "session", 30)
    assert server.proof_valid == [True]  # the server verified the proof of possession
    body = server.bodies[0]
    assert body["key"] == KEY and body["agent_version"]
    assert set(body["os_info"]) <= {"system", "release", "hostname", "python"}  # type: ignore[call-overload]

    saved = AgentSettings()
    assert saved.enrolled and saved.machine_id == MACHINE_ID and saved.mode == "session"
    assert saved.server_url == SERVER  # normalised: no trailing slash
    assert saved.agent_account == "tester"
    # What was sent as the public key is the half of the key now on disk.
    on_disk = KeyStore(saved.keys_dir, agent_account="tester").load()
    assert base64.b64decode(str(body["public_key"])) == _public(on_disk)
    assert not (saved.keys_dir / "machine.key.new").exists()
    # The one-time enrollment key is not kept anywhere.
    assert KEY not in saved.config_path.read_text("utf-8")


def test_a_refused_key_leaves_the_machine_exactly_as_it_was(settings: AgentSettings) -> None:
    with pytest.raises(EnrollmentRefused, match="recusou a chave de registro"):
        _enroll(settings, EnrollServer(refuse=True))
    assert not settings.config_path.exists()
    assert not settings.key_path.exists()
    assert not (settings.keys_dir / "machine.key.new").exists()


def test_an_unreachable_server_leaves_nothing_behind(settings: AgentSettings) -> None:
    server = EnrollServer(status=503)
    with pytest.raises(ServerUnavailable):
        _enroll(settings, server)
    assert server.calls == 3  # retried, then given up
    assert not settings.key_path.exists() and not settings.config_path.exists()


def test_a_machine_that_is_already_enrolled_is_not_overwritten_silently(
    settings: AgentSettings,
) -> None:
    _enroll(settings, EnrollServer())
    first_key = settings.key_path.read_bytes()

    again = EnrollServer()
    with pytest.raises(AgentError, match="clonada"):
        _enroll(AgentSettings(), again)
    assert again.calls == 0  # it did not even use up a key
    assert settings.key_path.read_bytes() == first_key


def test_force_replaces_the_identity(settings: AgentSettings) -> None:
    _enroll(settings, EnrollServer())
    first_key = settings.key_path.read_bytes()
    _enroll(AgentSettings(), EnrollServer(), force=True)
    assert settings.key_path.read_bytes() != first_key


def test_a_failed_forced_enrollment_keeps_the_working_identity(settings: AgentSettings) -> None:
    _enroll(settings, EnrollServer())
    store = KeyStore(settings.keys_dir, agent_account="tester")
    original = store.load().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )

    with pytest.raises(EnrollmentRefused):
        _enroll(AgentSettings(), EnrollServer(refuse=True), force=True)

    survivor = store.load().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    assert survivor == original
    assert AgentSettings().machine_id == MACHINE_ID


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://regista.exemplo.com.br", "https://regista.exemplo.com.br"),
        ("https://regista.exemplo.com.br/", "https://regista.exemplo.com.br"),
        ("  https://regista.exemplo.com.br///  ", "https://regista.exemplo.com.br"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_the_url_is_normalised_the_way_the_server_compares_it(given: str, expected: str) -> None:
    assert normalize_url(given) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "regista.exemplo.com.br",
        "ftp://regista.exemplo.com.br",
        "http://regista.exemplo.com.br",
        "https://",
    ],
)
def test_a_url_that_is_not_https_is_refused(bad: str) -> None:
    with pytest.raises(AgentError, match="https://"):
        normalize_url(bad)


@pytest.mark.skipif(sys.platform != "win32", reason="the account rule is about Windows services")
def test_session_mode_needs_the_dedicated_users_account(settings: AgentSettings) -> None:
    with pytest.raises(AgentError, match="--agent-account"):
        _enroll(settings, EnrollServer(mode="session"), account=None)
    assert not settings.key_path.exists() and not settings.config_path.exists()
    assert "--agent-account" in SESSION_NEEDS_ACCOUNT

    result = _enroll(settings, EnrollServer(mode="session"), account="usuario-dedicado")
    assert result.agent_account == "usuario-dedicado"


@pytest.mark.skipif(sys.platform != "win32", reason="the default account is a Windows service")
def test_service_mode_defaults_to_the_virtual_service_account(settings: AgentSettings) -> None:
    result = _enroll(settings, EnrollServer(mode="service"), account=None)
    assert result.agent_account == r"NT SERVICE\RegistaAgent"
    assert AgentSettings().agent_account == r"NT SERVICE\RegistaAgent"
