"""Request authentication and the route markers.

Every route declares exactly one marker in its dependencies. The isolation sweep in the tests
finds routes and their markers by walking `app.routes`, so a route without a marker fails there.

- `PublicRoute`: no session (login, invitation).
- `RequireStage(...)`: a session in one of the given stages (partial login steps, `/auth/me`).
- `SelfService`: an active session, any role (own account).
- `Require(permission)`: an active session whose role holds the permission.

`Auth.scope` is the client the request works on (see `auth/context.py`); handlers read with
`auth.scoped()`, write with `auth.writing()` and touch the user's own account with `auth.own()`.
"""

import hmac
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.auth.context import ClientScope, resolve_scope
from regista_api.auth.permissions import Permission, has_permission
from regista_api.auth.rate_limit import client_ip
from regista_api.auth.sessions import ALL_STAGES, SESSION_COOKIE
from regista_api.core.config import Settings
from regista_api.core.db import tenant_session
from regista_api.core.email import EmailSender
from regista_api.core.keys import KeyProvider
from regista_api.core.security import hash_token

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"
_TOUCH_EVERY = timedelta(seconds=60)


def api_error(status_code: int, code: str, **extra: Any) -> HTTPException:
    """Errors carry a stable machine code; the panel maps it to the design-system texts."""
    return HTTPException(status_code=status_code, detail={"code": code, **extra})


@dataclass(frozen=True)
class AppState:
    settings: Settings
    factory: async_sessionmaker[AsyncSession]
    keys: KeyProvider
    email: EmailSender


def get_state(request: Request) -> AppState:
    s = request.app.state
    return AppState(
        settings=s.settings, factory=s.session_factory, keys=s.key_provider, email=s.email_sender
    )


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    email: str
    role: str
    is_platform_admin: bool
    display_name: str | None
    status: str
    password_hash: str | None
    mfa_enabled: bool
    mfa_enabled_at: datetime | None
    mfa_secret_enc: bytes | None
    mfa_key_id: str | None
    mfa_last_step: int | None
    failed_logins: int
    locked_until: datetime | None


@dataclass(frozen=True)
class SessionInfo:
    id: uuid.UUID
    stage: str
    last_seen_at: datetime


@dataclass(frozen=True)
class Auth:
    user: CurrentUser
    session: SessionInfo
    scope: ClientScope
    state: AppState
    ip: str | None
    user_agent: str | None

    def own(self) -> AbstractAsyncContextManager[AsyncSession]:
        """A transaction in the user's own tenant (account, MFA and session operations)."""
        return tenant_session(self.state.factory, tenant_id=self.user.tenant_id)

    def scoped(self) -> AbstractAsyncContextManager[AsyncSession]:
        """A transaction for reading in the current client context.

        A client user (or a platform admin who picked a client) is bound to one tenant by RLS.
        "All clients" gets the platform-admin read flag instead, which RLS only honours for
        SELECT.
        """
        return tenant_session(
            self.state.factory,
            tenant_id=self.scope.tenant_id,
            platform_admin=self.scope.all_clients,
        )

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[AsyncSession]:
        """A transaction for writing in the current client. "All clients" is read-only."""
        if self.scope.tenant_id is None:
            raise api_error(409, "client_context_required")
        async with tenant_session(self.state.factory, tenant_id=self.scope.tenant_id) as db:
            yield db


_USER_SQL = text(
    "SELECT u.id, u.tenant_id, t.name AS tenant_name, u.email::text AS email, u.role,"
    " u.is_platform_admin, u.display_name, u.status, u.password_hash, u.mfa_enabled,"
    " u.mfa_enabled_at, u.mfa_secret_enc, u.mfa_key_id, u.mfa_last_step, u.failed_logins,"
    " u.locked_until, t.is_active AS tenant_active"
    " FROM users u JOIN tenants t ON t.id = u.tenant_id WHERE u.id = :id"
)


def _unauthorized() -> HTTPException:
    return api_error(401, "not_authenticated")


async def authenticate(
    request: Request, stages: tuple[str, ...], permission: Permission | None = None
) -> Auth:
    state = get_state(request)
    settings = state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 256:
        raise _unauthorized()

    # Before the tenant is known: the only lookup path is the minimal SECURITY DEFINER function.
    async with tenant_session(state.factory) as db:
        found = (
            (
                await db.execute(
                    text("SELECT * FROM app.lookup_session(:h)"), {"h": hash_token(token)}
                )
            )
            .mappings()
            .first()
        )
    if found is None:
        raise _unauthorized()

    now = datetime.now(UTC)
    if found["revoked_at"] is not None or found["expires_at"] <= now:
        raise _unauthorized()
    idle = timedelta(hours=settings.session_idle_hours)
    if found["stage"] == "active" and found["last_seen_at"] + idle <= now:
        raise _unauthorized()

    async with tenant_session(state.factory, tenant_id=found["tenant_id"]) as db:
        row = (await db.execute(_USER_SQL, {"id": found["user_id"]})).mappings().first()
        if row is None or row["status"] != "active" or not row["tenant_active"]:
            raise _unauthorized()
        if now - found["last_seen_at"] >= _TOUCH_EVERY:
            await db.execute(
                text("UPDATE sessions SET last_seen_at = now() WHERE id = :id"),
                {"id": found["session_id"]},
            )

    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not hmac.compare_digest(
            hash_token(header), bytes(found["csrf_token_hash"])
        ):
            raise api_error(403, "csrf_invalid")

    if found["stage"] not in stages:
        raise api_error(403, "session_stage", stage=found["stage"])

    if permission is not None and not has_permission(
        role=row["role"], is_platform_admin=row["is_platform_admin"], permission=permission
    ):
        raise api_error(403, "forbidden")

    scope = await resolve_scope(
        request,
        state.factory,
        is_platform_admin=row["is_platform_admin"],
        own_tenant_id=row["tenant_id"],
        own_tenant_name=row["tenant_name"],
    )
    user = CurrentUser(
        id=row["id"],
        tenant_id=row["tenant_id"],
        tenant_name=row["tenant_name"],
        email=row["email"],
        role=row["role"],
        is_platform_admin=row["is_platform_admin"],
        display_name=row["display_name"],
        status=row["status"],
        password_hash=row["password_hash"],
        mfa_enabled=row["mfa_enabled"],
        mfa_enabled_at=row["mfa_enabled_at"],
        mfa_secret_enc=bytes(row["mfa_secret_enc"]) if row["mfa_secret_enc"] else None,
        mfa_key_id=row["mfa_key_id"],
        mfa_last_step=row["mfa_last_step"],
        failed_logins=row["failed_logins"],
        locked_until=row["locked_until"],
    )
    return Auth(
        user=user,
        session=SessionInfo(
            id=found["session_id"], stage=found["stage"], last_seen_at=found["last_seen_at"]
        ),
        scope=scope,
        state=state,
        ip=client_ip(request, settings),
        user_agent=request.headers.get("user-agent"),
    )


class RouteMarker:
    """Base class of the markers the isolation sweep looks for."""

    kind = "marker"


class PublicRoute(RouteMarker):
    kind = "public"

    async def __call__(self, request: Request) -> None:
        # State-changing public routes only take JSON: a cross-site form post cannot send it.
        if request.method not in SAFE_METHODS:
            content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                raise api_error(415, "json_required")


class RequireStage(RouteMarker):
    kind = "stage"

    def __init__(self, *stages: str) -> None:
        self.stages = stages or ALL_STAGES

    async def __call__(self, request: Request) -> Auth:
        return await authenticate(request, self.stages)


class SelfService(RequireStage):
    """Active session, any role: operations on the user's own account."""

    kind = "self_service"

    def __init__(self) -> None:
        super().__init__("active")


class Require(RouteMarker):
    """Active session whose role grants `permission` (403 `forbidden` otherwise)."""

    kind = "permission"

    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    async def __call__(self, request: Request) -> Auth:
        return await authenticate(request, ("active",), self.permission)
