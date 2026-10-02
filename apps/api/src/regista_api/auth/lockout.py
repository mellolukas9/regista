"""Progressive per-account lockout (docs/specs/security.md).

After `lockout_threshold` consecutive failures (password or TOTP) the account is locked for
`lockout_base_seconds`, doubling on each further failure up to `lockout_max_seconds`.
Known and accepted MVP risk: someone can lock another person's account on purpose; the cap
and the per-e-mail rate limit bound the damage (docs/STATUS.md).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.audit import service as audit
from regista_api.core.config import Settings
from regista_api.core.db import tenant_session

_FAIL = text(
    "UPDATE users SET failed_logins = failed_logins + 1,"
    " locked_until = CASE WHEN failed_logins + 1 >= CAST(:thr AS integer)"
    "   THEN now() + make_interval(secs => LEAST(CAST(:cap AS double precision),"
    "     CAST(:base AS double precision) * power(2, failed_logins + 1 - CAST(:thr AS integer))))"
    "   ELSE locked_until END,"
    " updated_at = now()"
    " WHERE id = :id RETURNING failed_logins, locked_until"
)
_CLEAR = text(
    "UPDATE users SET failed_logins = 0, locked_until = NULL, updated_at = now() WHERE id = :id"
)


def is_locked(locked_until: datetime | None) -> bool:
    return locked_until is not None and locked_until > datetime.now(UTC)


async def record_failure(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    stage: str,
    ip: str | None,
) -> bool:
    """Count one failed attempt in its own transaction (it must persist even though the request
    is rejected). Returns True when the account just became locked."""
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        row = (
            await db.execute(
                _FAIL,
                {
                    "id": user_id,
                    "thr": settings.lockout_threshold,
                    "base": settings.lockout_base_seconds,
                    "cap": settings.lockout_max_seconds,
                },
            )
        ).one()
        locked = is_locked(row.locked_until)
        await audit.record(
            db,
            tenant_id=tenant_id,
            actor_type="user",
            actor_id=user_id,
            action="auth.login_failed",
            target_type="user",
            target_id=user_id,
            metadata={"stage": stage, "failed_logins": row.failed_logins, "locked": locked},
            ip=ip,
        )
    return locked


async def clear_failures(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(_CLEAR, {"id": user_id})
