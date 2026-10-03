"""Audit trail (docs/specs/data-model.md). `audit_log` is append-only for the app role: it can
INSERT but not SELECT, so rows are written with plain INSERT statements and never read back."""

import json
import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ActorType = Literal["user", "machine", "system"]

# Metadata is for facts about the action (ids, roles, methods). Never put secrets in it.
_INSERT = text(
    "INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, target_type, target_id,"
    " metadata, ip) VALUES (:tenant_id, :actor_type, :actor_id, :action, :target_type,"
    " :target_id, CAST(:metadata AS jsonb), CAST(:ip AS inet))"
)


async def record(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_type: ActorType,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    await db.execute(
        _INSERT,
        {
            "tenant_id": tenant_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "metadata": json.dumps(metadata or {}),
            "ip": ip,
        },
    )
