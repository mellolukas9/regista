"""Machine (agent) access tokens and the `MachineRoute` marker (docs/adr/0018).

The token is stateless: `rga1.<payload>.<mac>`, payload = `{typ, mid, tid, cv, exp}` as compact
JSON, MAC = HMAC-SHA256 under a key the KeyProvider derives for the purpose "agent-token". The
tenant of an agent request comes from this signed token, never from a parameter.

Signing is not enough to trust a token, so every request also reads the machine under RLS and
refuses it when it was revoked or when the credential was replaced (`cv` no longer matches).
That is what makes revocation and re-enrollment immediate.

A machine credential and a user session are never interchangeable: `MachineRoute` reads only
`Authorization: Bearer rga1...` and ignores cookies; `authenticate` (users) ignores the header.
"""

import base64
import binascii
import json
import uuid
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.auth.deps import AppState, RouteMarker, get_state
from regista_api.auth.rate_limit import client_ip
from regista_api.core.db import tenant_session
from regista_api.core.errors import api_error
from regista_api.core.keys import KeyProvider

TOKEN_PREFIX = "rga1"  # noqa: S105  (the public format prefix, not a secret)
MAC_PURPOSE = "agent-token"
_MAX_TOKEN_LENGTH = 1024


class MachineTokenError(Exception):
    """The token is malformed or its MAC does not verify."""


class MachineTokenExpired(MachineTokenError):
    """The MAC verifies but `exp` has passed: the agent should renew, not stop."""


@dataclass(frozen=True)
class MachineClaims:
    machine_id: uuid.UUID
    tenant_id: uuid.UUID
    credential_version: int
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise MachineTokenError("bad base64") from exc


def issue_machine_token(
    keys: KeyProvider,
    *,
    machine_id: uuid.UUID,
    tenant_id: uuid.UUID,
    credential_version: int,
    lifetime: timedelta,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Return `(token, expires_at)`."""
    expires_at = (now or datetime.now(UTC)) + lifetime
    payload = {
        "typ": "machine",
        "mid": str(machine_id),
        "tid": str(tenant_id),
        "cv": credential_version,
        "exp": int(expires_at.timestamp()),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signed = f"{TOKEN_PREFIX}.{body}"
    mac = keys.mac(MAC_PURPOSE, signed.encode())
    return f"{signed}.{_b64(mac)}", expires_at


def parse_machine_token(
    keys: KeyProvider, token: str, *, now: datetime | None = None
) -> MachineClaims:
    """Verify the MAC first, then read the claims. Raises `MachineTokenError` (or the expired
    subclass, only for a token that was genuinely ours)."""
    if len(token) > _MAX_TOKEN_LENGTH:
        raise MachineTokenError("too long")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise MachineTokenError("bad shape")
    signed = f"{parts[0]}.{parts[1]}"
    if not keys.verify_mac(MAC_PURPOSE, signed.encode(), _unb64(parts[2])):
        raise MachineTokenError("bad mac")
    try:
        payload = json.loads(_unb64(parts[1]))
        if payload["typ"] != "machine":
            raise MachineTokenError("wrong type")
        claims = MachineClaims(
            machine_id=uuid.UUID(payload["mid"]),
            tenant_id=uuid.UUID(payload["tid"]),
            credential_version=int(payload["cv"]),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), UTC),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise MachineTokenError("bad payload") from exc
    if claims.expires_at <= (now or datetime.now(UTC)):
        raise MachineTokenExpired("expired")
    return claims


@dataclass(frozen=True)
class MachineAuth:
    machine_id: uuid.UUID
    tenant_id: uuid.UUID
    credential_version: int
    mode: str
    status: str
    state: AppState
    ip: str | None

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        """A transaction bound to the machine's own tenant (RLS does the rest)."""
        return tenant_session(self.state.factory, tenant_id=self.tenant_id)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _unauthorized(code: str = "not_authenticated") -> HTTPException:
    return api_error(401, code)


_MACHINE_SQL = text(
    "SELECT status, revoked_at, credential_version, mode FROM machines WHERE id = :id"
)


async def authenticate_machine(request: Request) -> MachineAuth:
    state = get_state(request)
    token = _bearer(request)
    if token is None:
        raise _unauthorized()
    try:
        claims = parse_machine_token(state.keys, token)
    except MachineTokenExpired:
        raise _unauthorized("token_expired") from None
    except MachineTokenError:
        raise _unauthorized() from None

    async with tenant_session(state.factory, tenant_id=claims.tenant_id) as db:
        row = (await db.execute(_MACHINE_SQL, {"id": claims.machine_id})).mappings().first()
    if row is None:
        raise _unauthorized()
    if row["revoked_at"] is not None or row["status"] == "revoked":
        raise _unauthorized("machine_revoked")
    if row["credential_version"] != claims.credential_version:
        # The credential was replaced (re-enrollment): the old agent stops working at once.
        raise _unauthorized()
    return MachineAuth(
        machine_id=claims.machine_id,
        tenant_id=claims.tenant_id,
        credential_version=row["credential_version"],
        mode=row["mode"],
        status=row["status"],
        state=state,
        ip=client_ip(request, state.settings),
    )


class MachineRoute(RouteMarker):
    """An enrolled machine, authenticated by its access token (routes under `/agent/`)."""

    kind = "machine"

    async def __call__(self, request: Request) -> MachineAuth:
        return await authenticate_machine(request)
