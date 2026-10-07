"""Uploads of package versions that were started and never finished (ADR 0021).

A version is created `uploading` when the signature is accepted and becomes `published` only after
the server has read the object back. If the person closes the dialog or the browser fails, the row
stays `uploading`: this task, run by the worker, turns it `expired` once its URL has long expired
and deletes whatever object was left in the bucket. Same pattern as the other tasks that cross
clients (docs/adr/0018): read with the platform flag, write one client at a time.
"""

import uuid
from collections import defaultdict
from typing import Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

log = structlog.get_logger()

# The pre-signed URL lives 5 minutes; an hour later nobody can still be uploading with it.
GRACE_SECONDS = 3600

_SELECT = text(
    "SELECT tenant_id, id, storage_key FROM bot_versions"
    " WHERE status = 'uploading' AND upload_expires_at < now() - make_interval(secs => :grace)"
)
# The condition is repeated in the write: an upload completed meanwhile is left alone.
_EXPIRE = text(
    "UPDATE bot_versions SET status = 'expired' WHERE id = ANY(:ids) AND status = 'uploading'"
    " AND upload_expires_at < now() - make_interval(secs => :grace) RETURNING id, storage_key"
)


class ObjectStore(Protocol):
    async def delete(self, key: str) -> None: ...


async def expire_stale_uploads(
    factory: async_sessionmaker[AsyncSession],
    storage: ObjectStore,
    *,
    grace_seconds: int = GRACE_SECONDS,
) -> int:
    """Expire the abandoned uploads and delete their objects. Idempotent; returns how many."""
    async with tenant_session(factory, platform_admin=True) as db:
        rows = (await db.execute(_SELECT, {"grace": grace_seconds})).all()
    by_tenant: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for row in rows:
        by_tenant[row.tenant_id].append(row.id)

    expired = 0
    for tenant_id, ids in by_tenant.items():
        async with tenant_session(factory, tenant_id=tenant_id) as db:
            done = (await db.execute(_EXPIRE, {"ids": ids, "grace": grace_seconds})).all()
        for row in done:
            expired += 1
            try:
                await storage.delete(row.storage_key)
            except Exception as exc:
                log.warning(
                    "package_object_not_deleted", key=row.storage_key, error=type(exc).__name__
                )
    return expired
