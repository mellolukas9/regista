"""HTTPS to the server: proxy, system certificates, retries and the short-lived access token.

Every call is initiated by the agent (docs/specs/agent.md): the agent opens no port.

Two rules about time, because customers' clocks drift and get corrected while the agent runs:

- Renewing the token and backing off between retries use **elapsed time on a monotonic clock**
  (`time.monotonic`), never the system clock. Setting the clock forward or back changes nothing.
- The agent never reads the token's `exp`. It renews after `RENEW_AFTER_SECONDS` of its own time,
  and when the server says `token_expired` it renews at once. Only the server judges the token.

The token lives in memory only, never on disk.
"""

import random
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import truststore
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent import protocol
from regista_agent.config import AgentSettings
from regista_agent.errors import IdentityRejected, MachineRevoked, ServerUnavailable

# The server's token lives 15 minutes; renewing at 12 leaves room for a slow network.
RENEW_AFTER_SECONDS = 12 * 60
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0
DEFAULT_ATTEMPTS = 5
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

Clock = Callable[[], float]
Sleep = Callable[[float], None]


def error_code(response: httpx.Response) -> str | None:
    """The stable machine code of an API error (`{"detail": {"code": ...}}`), if there is one."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def make_client(settings: AgentSettings) -> httpx.Client:
    """An HTTP client for the server in `settings`.

    Certificates come from the operating system's store (`truststore`), which is what makes a
    corporate proxy that re-signs TLS work once the company's CA is installed; a CA bundle can
    be set instead. With no proxy configured the standard `HTTPS_PROXY` variables are honoured.
    """
    if settings.server_url is None:
        raise ValueError("server_url is not configured")
    verify: ssl.SSLContext
    if settings.ca_bundle is not None:
        verify = ssl.create_default_context(cafile=str(settings.ca_bundle))
    else:
        verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(
        base_url=protocol.audience(settings.server_url),
        timeout=httpx.Timeout(15.0, connect=8.0),
        verify=verify,
        proxy=settings.proxy,
        trust_env=True,
        follow_redirects=False,
        headers={"User-Agent": "regista-agent"},
    )


class HttpSession:
    """Requests with retries and exponential backoff (full jitter, capped)."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
        jitter: Callable[[], float] = random.random,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        self.client = client
        self.attempts = attempts
        self.clock = clock
        self._sleep = sleep
        self._jitter = jitter

    def backoff(self, attempt: int) -> float:
        """Seconds to wait before retry number `attempt` (0, 1, 2, ...)."""
        ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))
        return float(ceiling * (0.5 + self._jitter() / 2))

    def send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        attempts: int | None = None,
    ) -> httpx.Response:
        """One request, retried on network errors and on 429/5xx. Any other answer, including
        a 4xx, is returned for the caller to interpret."""
        attempts = attempts or self.attempts
        last: str = "sem resposta"
        for attempt in range(attempts):
            try:
                response = self.client.request(method, path, json=json, headers=headers)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}"
            else:
                if response.status_code not in _RETRY_STATUS:
                    return response
                last = f"HTTP {response.status_code}"
            if attempt + 1 < attempts:
                self._sleep(self.backoff(attempt))
        raise ServerUnavailable(f"Não foi possível falar com o servidor ({last}).")


@dataclass(frozen=True)
class HeartbeatInfo:
    heartbeat_seconds: int
    mode: str
    cancellations: list[str]


class AgentSession:
    """The agent's authenticated conversation with the server."""

    def __init__(
        self,
        http: HttpSession,
        *,
        machine_id: str,
        private_key: Ed25519PrivateKey,
        server_url: str,
    ) -> None:
        self.http = http
        self.machine_id = machine_id
        self._private_key = private_key
        self._audience = protocol.audience(server_url)
        self._token: str | None = None
        self._token_at: float | None = None  # monotonic time at which the token was obtained

    # --- token --------------------------------------------------------------------------------

    def _needs_token(self) -> bool:
        if self._token is None or self._token_at is None:
            return True
        return self.http.clock() - self._token_at >= RENEW_AFTER_SECONDS

    def login(self) -> None:
        challenge = self.http.send("POST", "/agent/challenge", json={"machine_id": self.machine_id})
        self._raise_if_rejected(challenge)
        if challenge.status_code != 200:
            raise ServerUnavailable(f"O servidor recusou o desafio (HTTP {challenge.status_code}).")
        nonce = challenge.json()["nonce"]
        signature = self._private_key.sign(
            protocol.auth_message(self.machine_id, nonce, self._audience)
        )
        exchanged = self.http.send(
            "POST",
            "/agent/token",
            json={
                "machine_id": self.machine_id,
                "nonce": nonce,
                "signature": protocol.b64(signature),
            },
        )
        self._raise_if_rejected(exchanged)
        if exchanged.status_code != 200:
            raise ServerUnavailable(
                f"O servidor recusou a troca de token (HTTP {exchanged.status_code})."
            )
        self._token = exchanged.json()["access_token"]
        self._token_at = self.http.clock()

    @staticmethod
    def _raise_if_rejected(response: httpx.Response) -> None:
        # Revoked, replaced and unknown machines all get this same answer: it is over for this
        # identity, whichever of them it is.
        if response.status_code == 401 and error_code(response) == "invalid_credentials":
            raise IdentityRejected

    # --- calls --------------------------------------------------------------------------------

    def request(self, method: str, path: str, *, json: Any = None) -> httpx.Response:
        """An authenticated call. Renews the token when it is old, or when the server says it
        expired, and stops for good when the server says the machine was revoked."""
        renewed = False
        while True:
            if self._needs_token():
                self.login()
                renewed = True
            response = self.http.send(
                method, path, json=json, headers={"Authorization": f"Bearer {self._token}"}
            )
            if response.status_code != 401:
                return response
            code = error_code(response)
            if code == "machine_revoked":
                raise MachineRevoked
            if code in ("token_expired", "not_authenticated") and not renewed:
                # One fresh token, then try again. `not_authenticated` is what a token whose
                # credential was replaced gets; the login that follows settles which it is.
                self._token = None
                continue
            return response

    def heartbeat(
        self, *, agent_version: str, os_info: dict[str, str], interactive_session: bool
    ) -> HeartbeatInfo:
        response = self.request(
            "POST",
            "/agent/heartbeat",
            json={
                "agent_version": agent_version,
                "os_info": os_info,
                "interactive_session": interactive_session,
            },
        )
        if response.status_code != 200:
            raise ServerUnavailable(f"O servidor recusou o sinal (HTTP {response.status_code}).")
        body = response.json()
        return HeartbeatInfo(
            heartbeat_seconds=int(body["heartbeat_seconds"]),
            mode=str(body["mode"]),
            cancellations=list(body.get("cancellations", [])),
        )
