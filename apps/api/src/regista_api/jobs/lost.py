"""Runs whose machine went silent: they end as `machine_lost` (docs/specs/orchestration.md).

Runs in the worker, which has no user and no tenant, with the pattern of `machines/presence.py`
(docs/adr/0018): read across clients with the platform flag (RLS lets that widen SELECT only),
then write one client at a time in a session bound to that client's `tenant_id`, so a write can
never reach another client's rows.

A cancellation the panel already asked for wins over `machine_lost`: the person wanted it stopped,
and it is.
"""

import uuid
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.audit import service as audit
from regista_api.core.db import tenant_session

_SELECT_LOST = text(
    "SELECT j.tenant_id, j.id FROM jobs j"
    " JOIN machines m ON m.tenant_id = j.tenant_id AND m.id = j.machine_id"
    " WHERE j.status IN ('assigned', 'running') AND m.status = 'offline'"
)
# The conditions are repeated in the write on purpose: a heartbeat that arrives between the read
# and the write brings the machine back online, and then nothing here may touch its run.
_END = text(
    "UPDATE jobs j SET"
    " status = CASE WHEN j.cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE 'failed' END,"
    " error_code = CASE WHEN j.cancel_requested_at IS NOT NULL THEN NULL"
    "                   ELSE 'machine_lost' END,"
    " finished_at = now(), updated_at = now()"
    " WHERE j.id = ANY(:ids) AND j.status IN ('assigned', 'running')"
    " AND EXISTS (SELECT 1 FROM machines m WHERE m.tenant_id = j.tenant_id"
    "             AND m.id = j.machine_id AND m.status = 'offline')"
    " RETURNING j.id, j.status, j.machine_id"
)


async def end_runs_of_lost_machines(factory: async_sessionmaker[AsyncSession]) -> int:
    """Return how many runs were ended. Idempotent."""
    async with tenant_session(factory, platform_admin=True) as db:
        rows = (await db.execute(_SELECT_LOST)).all()

    by_tenant: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for row in rows:
        by_tenant[row.tenant_id].append(row.id)

    ended = 0
    for tenant_id, job_ids in by_tenant.items():
        async with tenant_session(factory, tenant_id=tenant_id) as db:
            for job in (await db.execute(_END, {"ids": job_ids})).all():
                await audit.record(
                    db,
                    tenant_id=tenant_id,
                    actor_type="system",
                    actor_id=None,
                    action=f"job.{job.status}",
                    target_type="job",
                    target_id=job.id,
                    metadata={"because": "machine_lost", "machine_id": str(job.machine_id)},
                )
                ended += 1
    return ended


async def ensure_log_partitions(
    factory: async_sessionmaker[AsyncSession], *, months_ahead: int = 3
) -> int:
    """Create the monthly partitions of `job_logs` up to `months_ahead` months ahead. The function
    is fixed DDL owned by the schema owner (ADR 0020); the app role only calls it."""
    async with tenant_session(factory) as db:
        created: int = (
            await db.execute(text("SELECT app.ensure_job_log_partitions(:n)"), {"n": months_ahead})
        ).scalar_one()
    return created
