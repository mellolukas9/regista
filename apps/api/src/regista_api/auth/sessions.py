"""Server-side sessions. Only the sha256 of the token is stored; the cookie carries the token."""

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.core.config import Settings
from regista_api.core.security import hash_token, new_token

SESSION_COOKIE = "regista_session"
CSRF_COOKIE = "regista_csrf"
CLIENT_COOKIE = "rg_client"

# mfa_required | mfa_setup | recovery_codes | active
Stage = str
ALL_STAGES = ("mfa_required", "mfa_setup", "recovery_codes", "active")

_USER_AGENT_MAX = 512


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf: str
    expires_at: datetime

    @property
    def max_age(self) -> int:
        return max(0, int((self.expires_at - datetime.now(UTC)).total_seconds()))


def parse_ip(value: str | None) -> str | None:
    """Only well-formed addresses reach the `inet` column."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _expiry(stage: Stage, settings: Settings) -> datetime:
    if stage == "active":
        return datetime.now(UTC) + timedelta(days=settings.session_absolute_days)
    return datetime.now(UTC) + timedelta(minutes=settings.partial_session_minutes)


async def create_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    stage: Stage,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> IssuedSession:
    issued = IssuedSession(new_token(), new_token(), _expiry(stage, settings))
    await db.execute(
        text(
            "INSERT INTO sessions (tenant_id, user_id, token_hash, csrf_token_hash, stage,"
            " expires_at, ip, user_agent) VALUES (:tenant_id, :user_id, :token_hash, :csrf_hash,"
            " :stage, :expires_at, CAST(:ip AS inet), :user_agent)"
        ),
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "token_hash": hash_token(issued.token),
            "csrf_hash": hash_token(issued.csrf),
            "stage": stage,
            "expires_at": issued.expires_at,
            "ip": parse_ip(ip),
            "user_agent": (user_agent or "")[:_USER_AGENT_MAX] or None,
        },
    )
    return issued


async def rotate_session(
    db: AsyncSession, *, session_id: uuid.UUID, stage: Stage, settings: Settings
) -> IssuedSession:
    """New token and CSRF token on every stage change (prevents session fixation)."""
    issued = IssuedSession(new_token(), new_token(), _expiry(stage, settings))
    await db.execute(
        text(
            "UPDATE sessions SET token_hash = :token_hash, csrf_token_hash = :csrf_hash,"
            " stage = :stage, expires_at = :expires_at, last_seen_at = now() WHERE id = :id"
        ),
        {
            "id": session_id,
            "token_hash": hash_token(issued.token),
            "csrf_hash": hash_token(issued.csrf),
            "stage": stage,
            "expires_at": issued.expires_at,
        },
    )
    return issued


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE sessions SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"),
        {"id": session_id},
    )


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, except_session_id: uuid.UUID | None = None
) -> int:
    result = await db.execute(
        text(
            "UPDATE sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"
            " AND (CAST(:keep AS uuid) IS NULL OR id <> CAST(:keep AS uuid))"
        ),
        {"u": user_id, "keep": except_session_id},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def apply_cookies(response: Response, settings: Settings, issued: IssuedSession) -> None:
    secure = settings.cookie_secure
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        max_age=issued.max_age,
        secure=secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    # Readable by the panel's JS on purpose: it echoes the value in `X-CSRF-Token`.
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf,
        max_age=issued.max_age,
        secure=secure,
        httponly=False,
        samesite="lax",
        path="/",
    )


def clear_cookies(response: Response, settings: Settings) -> None:
    for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=http_only, samesite="lax"
        )
