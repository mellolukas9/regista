"""`regista-agent diagnose`: each way a machine can fail to reach the server, said in Portuguese."""

import socket
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from regista_agent import diagnose as diag
from regista_agent.config import AgentSettings, save_identity
from regista_agent.diagnose import CertInfo, Check, Probes, run_checks
from regista_agent.keystore import AclReport, KeyStore

SERVER = "https://regista.exemplo.com.br"
MACHINE_ID = uuid.uuid4()
NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _server(
    *,
    health: int = 200,
    skew: timedelta | None = timedelta(0),
    login: str = "ok",
) -> httpx.Client:
    """A server for the checks: /health (with a Date header) and the two login calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            headers = {"date": format_datetime(NOW + skew, usegmt=True)} if skew is not None else {}
            return httpx.Response(health, json={"status": "ok"}, headers=headers)
        if request.url.path == "/agent/challenge":
            if login == "rejected":
                return httpx.Response(401, json={"detail": {"code": "invalid_credentials"}})
            return httpx.Response(200, json={"nonce": "bm9uY2U=", "expires_in": 60})
        if request.url.path == "/agent/token":
            return httpx.Response(200, json={"access_token": "rga1.x.y", "expires_in": 900})
        return httpx.Response(404)

    return httpx.Client(base_url=SERVER, transport=httpx.MockTransport(handler))


def _probes(client: httpx.Client | None = None, **overrides: object) -> Probes:
    values: dict[str, object] = {
        "resolve": lambda host, port: ["203.0.113.10"],
        "connect": lambda host, port: None,
        "peer_certificate": lambda host, port, settings: CertInfo(
            "regista.exemplo.com.br", "Let's Encrypt", "2026-12-31"
        ),
        "which": lambda name: "C:/tools/uv.exe",
        "now": lambda: NOW,
        "client": lambda settings: client or _server(),
        "python": (3, 13, 1),
        **overrides,
    }
    return Probes(**values)  # type: ignore[arg-type]


@pytest.fixture
def enrolled(home: Path, no_acl: None, monkeypatch: pytest.MonkeyPatch) -> AgentSettings:
    save_identity(
        home, server_url=SERVER, machine_id=MACHINE_ID, mode="service", agent_account="tester"
    )
    settings = AgentSettings()
    store = KeyStore(settings.keys_dir, agent_account="tester")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    store.commit(store.stage(Ed25519PrivateKey.generate()))
    monkeypatch.setattr(KeyStore, "check_acl", lambda self: AclReport([]))
    return settings


def _by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def test_a_healthy_machine_has_nothing_to_report(enrolled: AgentSettings) -> None:
    checks = run_checks(enrolled, _probes())
    assert [c.status for c in checks if c.status != "ok"] == []
    assert diag.exit_code(checks) == 0
    names = [c.name for c in checks]
    assert names == [
        "Python",
        "uv",
        "Cadastro",
        "Proxy",
        "DNS",
        "Conexão TCP",
        "TLS",
        "Servidor",
        "Horário",
        "Chave da máquina",
        "Login",
    ]
    text = diag.format_checks(checks)
    assert "[OK   ] Login: O servidor aceitou a identidade desta máquina." in text


def test_a_machine_that_was_never_enrolled_says_so(home: Path) -> None:
    checks = _by_name(run_checks(AgentSettings(), _probes()))
    assert (
        checks["Cadastro"].status == "erro" and "regista-agent enroll" in checks["Cadastro"].detail
    )
    assert checks["Rede"].status == "aviso"  # no server URL: the network checks were skipped
    assert diag.exit_code(list(checks.values())) == 1


def test_dns_and_tcp_failures_are_told_apart(enrolled: AgentSettings) -> None:
    def no_dns(host: str, port: int) -> list[str]:
        raise socket.gaierror(11001, "getaddrinfo failed")

    def refused(host: str, port: int) -> None:
        raise ConnectionRefusedError("recusada")

    dns = _by_name(run_checks(enrolled, _probes(resolve=no_dns)))
    assert dns["DNS"].status == "erro" and "regista.exemplo.com.br" in dns["DNS"].detail
    assert dns["Conexão TCP"].status == "ok"

    tcp = _by_name(run_checks(enrolled, _probes(connect=refused)))
    assert tcp["Conexão TCP"].status == "erro" and "443" in tcp["Conexão TCP"].detail
    assert tcp["DNS"].status == "ok"


def test_an_untrusted_certificate_points_at_the_corporate_proxy(enrolled: AgentSettings) -> None:
    def untrusted(host: str, port: int, settings: AgentSettings) -> CertInfo:
        raise ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")

    tls = _by_name(run_checks(enrolled, _probes(peer_certificate=untrusted)))["TLS"]
    assert tls.status == "erro"
    assert "CA da empresa" in tls.detail and "REGISTA_CA_BUNDLE" in tls.detail

    def handshake(host: str, port: int, settings: AgentSettings) -> CertInfo:
        raise ssl.SSLError("handshake failure")

    assert (
        _by_name(run_checks(enrolled, _probes(peer_certificate=handshake)))["TLS"].status == "erro"
    )


def test_plain_http_is_only_a_warning_for_development(home: Path) -> None:
    save_identity(
        home,
        server_url="http://127.0.0.1:8000",
        machine_id=MACHINE_ID,
        mode="service",
        agent_account=None,
    )
    checks = _by_name(run_checks(AgentSettings(), _probes()))
    assert checks["TLS"].status == "aviso" and "desenvolvimento" in checks["TLS"].detail


def test_a_proxy_changes_what_can_be_tested_directly(
    enrolled: AgentSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.empresa:3128")
    dialled: list[tuple[str, int]] = []
    checks = _by_name(
        run_checks(
            AgentSettings(),
            _probes(connect=lambda host, port: dialled.append((host, port))),
        )
    )
    assert "proxy.empresa" in checks["Proxy"].detail
    assert checks["DNS"].status == "aviso" and checks["TLS"].status == "aviso"
    assert dialled == [("proxy.empresa", 3128)]  # the proxy is what it dials, not the server


def test_a_server_that_does_not_answer_health_is_an_error(enrolled: AgentSettings) -> None:
    down = _by_name(run_checks(enrolled, _probes(_server(health=503))))
    assert down["Servidor"].status == "erro" and "503" in down["Servidor"].detail

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url=SERVER, transport=httpx.MockTransport(unreachable))
    gone = _by_name(run_checks(enrolled, _probes(client)))
    assert gone["Servidor"].status == "erro" and "ConnectError" in gone["Servidor"].detail
    assert gone["Horário"].status == "aviso"  # no response, no Date header to compare


def test_a_wrong_clock_is_a_warning_with_the_difference(enrolled: AgentSettings) -> None:
    off = _by_name(run_checks(enrolled, _probes(_server(skew=timedelta(minutes=5)))))["Horário"]
    assert off.status == "aviso" and "300 s" in off.detail

    near = _by_name(run_checks(enrolled, _probes(_server(skew=timedelta(seconds=40)))))["Horário"]
    assert near.status == "ok"

    missing = _by_name(run_checks(enrolled, _probes(_server(skew=None))))["Horário"]
    assert missing.status == "aviso"


def test_old_python_is_an_error_and_a_missing_uv_a_warning(enrolled: AgentSettings) -> None:
    checks = _by_name(run_checks(enrolled, _probes(python=(3, 10, 0), which=lambda name: None)))
    assert checks["Python"].status == "erro" and "3.12" in checks["Python"].detail
    assert checks["uv"].status == "aviso"
    assert diag.exit_code(list(checks.values())) == 1


def test_a_key_that_is_too_open_is_an_error(
    enrolled: AgentSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        KeyStore,
        "check_acl",
        lambda self: AclReport(["A pasta da chave herda permissões da pasta de cima."]),
    )
    key = _by_name(run_checks(enrolled, _probes()))["Chave da máquina"]
    assert key.status == "erro" and "herda" in key.detail


def test_no_key_yet_is_only_a_warning(home: Path) -> None:
    save_identity(
        home, server_url=SERVER, machine_id=MACHINE_ID, mode="service", agent_account=None
    )
    checks = _by_name(run_checks(AgentSettings(), _probes()))
    assert checks["Chave da máquina"].status == "aviso"
    assert "Login" not in checks  # nothing to log in with


def test_a_machine_the_server_rejects_gets_a_clear_login_error(enrolled: AgentSettings) -> None:
    login = _by_name(run_checks(enrolled, _probes(_server(login="rejected"))))["Login"]
    assert login.status == "erro" and "revogada" in login.detail
    assert diag.exit_code([login]) == 1


def test_the_report_has_one_line_per_check_and_a_verdict_by_exit_code() -> None:
    checks = [Check("A", "ok", "bem"), Check("B", "aviso", "atenção"), Check("C", "erro", "mal")]
    assert diag.format_checks(checks).splitlines() == [
        "[OK   ] A: bem",
        "[AVISO] B: atenção",
        "[ERRO ] C: mal",
    ]
    assert diag.exit_code(checks) == 1
    assert diag.exit_code(checks[:2]) == 0
