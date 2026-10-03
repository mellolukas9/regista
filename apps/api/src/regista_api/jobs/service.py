"""Creating and finishing runs. Shared by the panel routes, the agent routes and the worker, so
the rules live in one place (docs/specs/orchestration.md)."""

import json
import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

_SHORT_CODE_TRIES = 8


def new_short_code() -> str:
    return f"exec-{secrets.token_hex(3)}"


async def create_job(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    pool_id: uuid.UUID,
    params: dict[str, Any],
    trigger: str,
    triggered_by: uuid.UUID | None,
) -> uuid.UUID:
    """A new `pending` run. "Executar agora" with a run already active creates another one: it
    waits its turn (design-system.md 11.2). The short code is random and unique per client; on the
    rare collision the insert is retried."""
    for _ in range(_SHORT_CODE_TRIES):
        try:
            async with db.begin_nested():
                job_id: uuid.UUID = (
                    await db.execute(
                        text(
                            "INSERT INTO jobs (tenant_id, bot_id, pool_id, short_code, trigger,"
                            " triggered_by, params) VALUES (:t, :b, :p, :c, :g, :u,"
                            " CAST(:x AS jsonb)) RETURNING id"
                        ),
                        {
                            "t": tenant_id,
                            "b": bot_id,
                            "p": pool_id,
                            "c": new_short_code(),
                            "g": trigger,
                            "u": triggered_by,
                            "x": json.dumps(params),
                        },
                    )
                ).scalar_one()
            return job_id
        except IntegrityError as exc:
            if "uq_jobs_tenant_short_code" not in str(exc.orig):
                raise
    raise RuntimeError("could not find a free short code")


async def finish_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    from_statuses: tuple[str, ...],
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    machine_id: uuid.UUID | None = None,
    when: datetime | None = None,
) -> bool:
    """Move a run to a final state, only from `from_statuses` (and from `machine_id`, when given).

    Returns False when nothing matched, so callers racing each other (the agent, a cancellation,
    the worker) never overwrite a state somebody else already settled. It is the one place where a
    run ends; M5 extends it to move the items still `in_progress` to `abandoned`.
    """
    result = await db.execute(
        text(
            "UPDATE jobs SET status = :s, finished_at = coalesce(CAST(:w AS timestamptz), now()),"
            " error_code = :c, error_message = :m, updated_at = now()"
            " WHERE id = :j AND status = ANY(:from)"
            " AND (CAST(:mid AS uuid) IS NULL OR machine_id = :mid) RETURNING id"
        ),
        {
            "s": status,
            "w": when,
            "c": error_code,
            "m": (error_message or "")[:1000] or None,
            "j": job_id,
            "from": list(from_statuses),
            "mid": machine_id,
        },
    )
    return result.first() is not None


async def cancellations(
    db: AsyncSession, machine_id: uuid.UUID, current_job_id: uuid.UUID | None
) -> list[uuid.UUID]:
    """Runs of this machine that must stop: those the panel asked to cancel, plus the one the
    agent says it is running when the server already ended it (lost, revoked, cancelled). Only
    runs of this machine are ever named, so an id sent by someone else agent echoes nothing."""
    rows = await db.execute(
        text(
            "SELECT id FROM jobs WHERE machine_id = :m AND ("
            " (status IN ('assigned', 'running') AND cancel_requested_at IS NOT NULL)"
            " OR (id = CAST(:cur AS uuid) AND status NOT IN ('assigned', 'running')))"
            " ORDER BY created_at"
        ),
        {"m": machine_id, "cur": current_job_id},
    )
    return [r.id for r in rows]
