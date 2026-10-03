"""The agent's transport: token lifecycle, revocation, retries, and time that cannot be bent."""

import base64
import json
import time
import uuid
from pathlib import Path

import certifi
import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent import protocol, transport
from regista_agent.config import AgentSettings
from regista_agent.errors import IdentityRejected, MachineRevoked, ServerUnavailable
from regista_agent.transport import (
    BACKOFF_CAP_SECONDS,
    RENEW_AFTER_SECONDS,
    AgentSession,
    HttpSession,
    error_code,
    make_client,
)

SERVER = "https://regista.exemplo.com.br"
MACHINE_ID = str(uuid.uuid4())


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeServer:
    """Plays the API's side of the agent protocol and records what it was asked."""

    def __init__(self, public_key: Ed25519PrivateKey) -> None:
        self.public = public_key.public_key()
        self.logins = 0
        self.tokens: list[str] = []
        self.heartbeats = 0
        self.script: list[httpx.Response] = []  # answers to heartbeats, taken first
        self.token_answers: list[httpx.Response] = []  # answers to the token exchange
        self.challenge_answers: list[httpx.Response] = []
        self.nonce = ""

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/agent/challenge":
            if self.challenge_answers:
                return self.challenge_answers.pop(0)
            self.nonce = base64.b64encode(uuid.uuid4().bytes * 2).decode()
            return httpx.Response(200, json={"nonce": self.nonce, "expires_in": 60})
        if path == "/agent/token":
            if self.token_answers:
                return self.token_answers.pop(0)
            data = json.loads(request.content)
            signature = base64.b64decode(data["signature"])
            message = protocol.auth_message(data["machine_id"], data["nonce"], SERVER)
            try:
                self.public.verify(signature, message)
            except InvalidSignature:
                return httpx.Response(401, json={"detail": {"code": "invalid_credentials"}})
            self.logins += 1
            token = f"rga1.token-{self.logins}.mac"
            self.tokens.append(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 900})
        if path == "/agent/heartbeat":
            self.heartbeats += 1
            if self.script:
                return self.script.pop(0)
            return httpx.Response(
                200,
                json={
                    "server_time": "2026-01-01T00:00:00Z",
                    "heartbeat_seconds": 30,
                    "mode": "service",
                    "cancellations": [],
                },
            )
        return httpx.Response(404)


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": {"code": code}})


class Rig:
    def __init__(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        self.server = FakeServer(self.private)
        self.clock = FakeClock()
        self.sleeps: list[float] = []
        client = httpx.Client(base_url=SERVER, transport=httpx.MockTransport(self.server.handler))
        self.http = HttpSession(
            client, clock=self.clock, sleep=self.sleeps.append, jitter=lambda: 1.0
        )
        self.session = AgentSession(
            self.http, machine_id=MACHINE_ID, private_key=self.private, server_url=SERVER + "/"
        )

    def beat(self) -> transport.HeartbeatInfo:
        return self.session.heartbeat(
            agent_version="0.1.0", os_info={"system": "Windows"}, interactive_session=True
        )


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --- login and token lifetime -----------------------------------------------------------------


def test_login_signs_the_challenge_the_way_the_server_verifies_it(rig: Rig) -> None:
    info = rig.beat()
    assert (info.heartbeat_seconds, info.mode, info.cancellations) == (30, "service", [])
    # The fake server verified the signature over the shared contract and the audience (the URL
    # given with a trailing slash counts as the same one).
    assert rig.server.logins == 1


def test_the_token_is_reused_until_it_is_old_then_renewed(rig: Rig) -> None:
    rig.beat()
    for _ in range(5):
        rig.clock.advance(30)
        rig.beat()
    assert rig.server.logins == 1

    rig.clock.advance(RENEW_AFTER_SECONDS)  # well past 12 minutes of the agent's own time
    rig.beat()
    assert rig.server.logins == 2


def test_the_new_token_is_the_one_that_gets_sent(rig: Rig) -> None:
    seen: list[str] = []
    original = rig.server.handler

    def spy(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/heartbeat":
            seen.append(request.headers["authorization"])
        return original(request)

    rig.http.client = httpx.Client(base_url=SERVER, transport=httpx.MockTransport(spy))
    rig.beat()
    rig.clock.advance(RENEW_AFTER_SECONDS)
    rig.beat()
    assert seen == ["Bearer rga1.token-1.mac", "Bearer rga1.token-2.mac"]


def test_renewal_does_not_depend_on_the_system_clock(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Customers' clocks drift and get corrected. With the system clock thrown ten years ahead,
    then ten years back, the agent renews exactly when its own elapsed time says so."""
    ten_years = 10 * 365 * 24 * 3600
    real = time.time()

    def renewals_with_system_clock_at(value: float) -> int:
        rig.server.logins = 0
        rig.session._token = None
        monkeypatch.setattr(time, "time", lambda: value)
        rig.beat()
        for _ in range(5):
            rig.clock.advance(60)
            rig.beat()
        rig.clock.advance(RENEW_AFTER_SECONDS)
        rig.beat()
        return rig.server.logins

    assert renewals_with_system_clock_at(real) == 2
    assert renewals_with_system_clock_at(real + ten_years) == 2
    assert renewals_with_system_clock_at(real - ten_years) == 2


def test_the_transport_never_reads_the_system_clock_or_the_token_expiry() -> None:
    """A guard on the source: time comes from the injected monotonic clock, and only the server
    looks inside the token."""
    source = Path(transport.__file__).read_text("utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    for forbidden in ("time.time(", "datetime", "utcnow", "perf_counter", '"exp"', "'exp'"):
        assert forbidden not in code, f"transport.py uses {forbidden!r}"
    assert "time.monotonic" in code


# --- the server says no -----------------------------------------------------------------------


def test_an_expired_token_is_renewed_once_and_the_call_goes_through(rig: Rig) -> None:
    rig.beat()
    rig.server.script.append(_error(401, "token_expired"))
    rig.beat()
    assert rig.server.logins == 2  # one fresh token
    assert rig.server.heartbeats == 3  # the refused call, and the retry


def test_a_token_that_is_still_refused_after_renewal_is_not_retried_forever(rig: Rig) -> None:
    rig.beat()
    rig.server.script.extend([_error(401, "token_expired"), _error(401, "token_expired")])
    with pytest.raises(ServerUnavailable):
        rig.beat()
    assert rig.server.logins == 2


def test_a_revoked_machine_stops_the_agent_at_once(rig: Rig) -> None:
    rig.beat()
    rig.server.script.append(_error(401, "machine_revoked"))
    with pytest.raises(MachineRevoked, match="foi revogada"):
        rig.beat()
    assert rig.server.logins == 1  # it did not even try to log in again


def test_a_replaced_credential_ends_in_a_rejected_identity(rig: Rig) -> None:
    """After a new enrollment elsewhere the old token is `not_authenticated`; logging in again
    then fails, which is how the old agent learns it is over."""
    rig.beat()
    rig.server.script.append(_error(401, "not_authenticated"))
    rig.server.challenge_answers.append(_error(401, "invalid_credentials"))
    with pytest.raises(IdentityRejected, match="revogada ou cadastrada"):
        rig.beat()
    assert isinstance(IdentityRejected(), MachineRevoked)  # same exit as a revocation


def test_a_machine_the_server_does_not_accept_never_gets_a_token(rig: Rig) -> None:
    rig.server.token_answers.append(_error(401, "invalid_credentials"))
    with pytest.raises(IdentityRejected):
        rig.beat()
    assert rig.server.heartbeats == 0


# --- retries ----------------------------------------------------------------------------------


def test_server_errors_are_retried_with_exponential_backoff(rig: Rig) -> None:
    rig.beat()
    rig.server.script.extend([httpx.Response(503), httpx.Response(502), httpx.Response(429)])
    rig.beat()
    assert rig.sleeps == [1.0, 2.0, 4.0]  # jitter pinned to its maximum


def test_backoff_is_capped_and_jittered(rig: Rig) -> None:
    assert [rig.http.backoff(a) for a in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert rig.http.backoff(50) == BACKOFF_CAP_SECONDS
    low = HttpSession(rig.http.client, jitter=lambda: 0.0)
    assert [low.backoff(a) for a in (0, 1, 2)] == [0.5, 1.0, 2.0]  # half, at the other extreme


def test_network_errors_are_retried_and_then_reported(rig: Rig) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = HttpSession(
        httpx.Client(base_url=SERVER, transport=httpx.MockTransport(refuse)),
        clock=rig.clock,
        sleep=rig.sleeps.append,
        jitter=lambda: 1.0,
    )
    with pytest.raises(ServerUnavailable, match="ConnectError"):
        http.send("GET", "/health", attempts=4)
    assert rig.sleeps == [1.0, 2.0, 4.0]  # three waits for four attempts


def test_a_client_error_is_returned_not_retried(rig: Rig) -> None:
    rig.server.script.append(_error(422, "invalid"))
    response = rig.session.request("POST", "/agent/heartbeat", json={})
    assert (response.status_code, error_code(response)) == (422, "invalid")
    assert rig.server.heartbeats == 1  # asked once
    assert rig.sleeps == []  # and never waited


# --- helpers ----------------------------------------------------------------------------------


def test_error_code_reads_the_api_error_shape_and_nothing_else() -> None:
    assert error_code(_error(401, "machine_revoked")) == "machine_revoked"
    assert error_code(httpx.Response(502, text="<html>bad gateway</html>")) is None
    assert error_code(httpx.Response(422, json={"detail": [{"msg": "x"}]})) is None
    assert error_code(httpx.Response(200, json=["not", "a", "dict"])) is None


def test_the_client_uses_the_configured_server_proxy_and_ca_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REGISTA_HOME", str(tmp_path))
    settings = AgentSettings(server_url=SERVER + "/", proxy="http://proxy.local:3128")
    client = make_client(settings)
    assert str(client.base_url) == SERVER  # normalised: the trailing slash of the setting is gone
    client.close()

    # A CA bundle replaces the system store; a real one loads, a missing one fails at once.
    with_bundle = make_client(AgentSettings(server_url=SERVER, ca_bundle=Path(certifi.where())))
    with_bundle.close()
    with pytest.raises(OSError):
        make_client(AgentSettings(server_url=SERVER, ca_bundle=tmp_path / "missing.pem"))


def test_a_signature_of_another_key_would_be_refused(rig: Rig) -> None:
    stranger = AgentSession(
        rig.http,
        machine_id=MACHINE_ID,
        private_key=Ed25519PrivateKey.generate(),
        server_url=SERVER,
    )
    with pytest.raises(IdentityRejected):
        stranger.heartbeat(agent_version="0", os_info={}, interactive_session=False)
