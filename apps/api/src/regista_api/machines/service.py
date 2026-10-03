"""Shared machine operations: history events and enrollment keys (docs/specs/agent.md)."""

import json
import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.core.config import Settings
from regista_api.core.security import hash_token

KEY_PREFIX = "rgk_"


async def record_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    machine_id: uuid.UUID,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a history event. `machine_events` is insert-only for the app role."""
    await db.execute(
        text(
            "INSERT INTO machine_events (tenant_id, machine_id, kind, metadata)"
            " VALUES (:t, :m, :k, CAST(:meta AS jsonb))"
        ),
        {"t": tenant_id, "m": machine_id, "k": kind, "meta": json.dumps(metadata or {})},
    )


async def issue_enrollment_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    machine_id: uuid.UUID,
    created_by: uuid.UUID,
    settings: Settings,
) -> tuple[str, datetime]:
    """Revoke the machine's live key (if any) and create a new one, in the caller's transaction.

    A machine has at most one live key (partial unique index), so the old one has to go first.
    Only the sha256 is stored; the plaintext is returned to the caller once and nowhere else.
    """
    await db.execute(
        text(
            "UPDATE enrollment_keys SET revoked_at = now()"
            " WHERE machine_id = :m AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {"m": machine_id},
    )
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    expires_at: datetime = (
        await db.execute(
            text(
                "INSERT INTO enrollment_keys (tenant_id, machine_id, key_hash, expires_at,"
                " created_by) VALUES (:t, :m, :h, now() + make_interval(hours => :hours), :u)"
                " RETURNING expires_at"
            ),
            {
                "t": tenant_id,
                "m": machine_id,
                "h": hash_token(key),
                "hours": settings.enrollment_key_hours,
                "u": created_by,
            },
        )
    ).scalar_one()
    return key, expires_at
