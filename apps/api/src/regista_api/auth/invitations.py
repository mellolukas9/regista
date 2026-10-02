import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.core.config import Settings
from regista_api.core.security import hash_token, new_token


async def issue_invitation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    created_by: uuid.UUID | None,
    settings: Settings,
) -> str:
    """Create a single-use invitation and return the raw token (shown only in the e-mail link).

    Any earlier unused invitation of the same user stops working.
    """
    await db.execute(
        text(
            "UPDATE invitations SET revoked_at = now()"
            " WHERE user_id = :u AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {"u": user_id},
    )
    token = new_token()
    await db.execute(
        text(
            "INSERT INTO invitations (tenant_id, user_id, token_hash, expires_at, created_by)"
            " VALUES (:t, :u, :h, :exp, :by)"
        ),
        {
            "t": tenant_id,
            "u": user_id,
            "h": hash_token(token),
            "exp": datetime.now(UTC) + timedelta(days=settings.invitation_days),
            "by": created_by,
        },
    )
    return token
