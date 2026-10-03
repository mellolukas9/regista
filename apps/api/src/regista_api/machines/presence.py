"""Machine presence: "Sem sinal" after a silence (docs/specs/orchestration.md, docs/adr/0018).

Runs in the worker, which has no user and no tenant. The pattern for work that crosses clients:
read with the platform flag (RLS lets that widen SELECT only), then write one client at a time in
a session bound to that client's `tenant_id`, so a write can never reach another client's rows.
"""

import uuid
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_api.machines import service

# The silence condition is repeated in the write on purpose: it is what keeps the sweep from
# racing a heartbeat that arrives between the read and the write.
_SELECT_STALE = text(
    "SELECT tenant_id, id FROM machines"
    " WHERE status = 'online' AND last_seen_at < now() - make_interval(secs => :silent)"
)
_MARK_OFFLINE = text(
    "UPDATE machines SET status = 'offline', updated_at = now() WHERE id = ANY(:ids)"
    " AND status = 'online' AND last_seen_at < now() - make_interval(secs => :silent)"
    " RETURNING id"
)


async def mark_stale_machines_offline(
    factory: async_sessionmaker[AsyncSession], *, offline_after_seconds: int
) -> int:
    """Turn every online machine that has been silent for `offline_after_seconds` offline and
    record `went_offline`. Returns how many machines changed.

    Idempotent, and safe against a heartbeat that arrives meanwhile: the write repeats the
    condition, so a machine that just spoke is left alone and gets no event.
    """
    async with tenant_session(factory, platform_admin=True) as db:
        rows = (await db.execute(_SELECT_STALE, {"silent": offline_after_seconds})).all()

    by_tenant: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for row in rows:
        by_tenant[row.tenant_id].append(row.id)

    changed = 0
    for tenant_id, machine_ids in by_tenant.items():
        async with tenant_session(factory, tenant_id=tenant_id) as db:
            updated = (
                await db.execute(
                    _MARK_OFFLINE, {"ids": machine_ids, "silent": offline_after_seconds}
                )
            ).all()
            for row in updated:
                await service.record_event(
                    db, tenant_id=tenant_id, machine_id=row.id, kind="went_offline"
                )
            changed += len(updated)
    return changed


async def purge_auth_rate_limits(
    factory: async_sessionmaker[AsyncSession], *, older_than_seconds: int
) -> int:
    """Remove old windows of the rate limit table (it has no grant for the app role, only the
    purge function reaches it). Returns how many rows went."""
    async with tenant_session(factory) as db:
        removed: int = (
            await db.execute(text("SELECT app.rate_limit_purge(:s)"), {"s": older_than_seconds})
        ).scalar_one()
    return removed
