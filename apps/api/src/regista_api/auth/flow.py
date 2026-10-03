"""Steps shared by the auth and account routes: rate limits, TOTP checks, recovery codes."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.auth import mfa
from regista_api.auth.deps import AppState, Auth
from regista_api.auth.lockout import is_locked, record_failure
from regista_api.auth.rate_limit import rate_limited
from regista_api.core.errors import api_error
from regista_api.core.security import hash_password, new_recovery_code, normalize_recovery_code

RECOVERY_CODE_COUNT = 10


async def limit(state: AppState, scope: str, subject: str, window: int, max_hits: int) -> None:
    if await rate_limited(
        state.factory, scope=scope, subject=subject, window_seconds=window, max_hits=max_hits
    ):
        raise api_error(429, "rate_limited")


async def check_secret_attempt(auth: Auth, scope: str, subject: str) -> None:
    """Rate limit plus lockout check before verifying a password or a code."""
    s = auth.state.settings
    await limit(auth.state, scope, subject, 300, s.rate_mfa_per_5min)
    if is_locked(auth.user.locked_until):
        raise api_error(429, "rate_limited")


async def fail_attempt(auth: Auth, stage: str | None = None) -> None:
    """Count a wrong password or code toward the progressive lockout."""
    await record_failure(
        auth.state.factory,
        auth.state.settings,
        tenant_id=auth.user.tenant_id,
        user_id=auth.user.id,
        stage=stage or auth.session.stage,
        ip=auth.ip,
    )


def totp_secret(auth: Auth) -> str:
    user = auth.user
    if not user.mfa_secret_enc or not user.mfa_key_id:
        raise api_error(409, "mfa_not_configured")
    return mfa.decrypt_secret(
        auth.state.keys, user.tenant_id, user.id, user.mfa_secret_enc, user.mfa_key_id
    )


def fresh_step(auth: Auth, code: str) -> int | None:
    """The TOTP step if the code is valid and newer than the last accepted one."""
    step = mfa.match_step(totp_secret(auth), code)
    last = auth.user.mfa_last_step
    return step if step is not None and (last is None or step > last) else None


async def claim_step(db: AsyncSession, auth: Auth, step: int) -> bool:
    """Atomically record the step; false when a concurrent request already used it."""
    result = await db.execute(
        text(
            "UPDATE users SET mfa_last_step = :step, updated_at = now() WHERE id = :id"
            " AND (mfa_last_step IS NULL OR mfa_last_step < :step) RETURNING id"
        ),
        {"id": auth.user.id, "step": step},
    )
    return result.first() is not None


async def replace_recovery_codes(db: AsyncSession, auth: Auth) -> list[str]:
    """Invalidate every existing code and store a new set; returns the plain codes once."""
    codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    hashes = await asyncio.to_thread(
        lambda: [hash_password(normalize_recovery_code(c)) for c in codes]
    )
    await db.execute(text("DELETE FROM recovery_codes WHERE user_id = :u"), {"u": auth.user.id})
    await db.execute(
        text("INSERT INTO recovery_codes (tenant_id, user_id, code_hash) VALUES (:t, :u, :h)"),
        [{"t": auth.user.tenant_id, "u": auth.user.id, "h": h} for h in hashes],
    )
    return codes
