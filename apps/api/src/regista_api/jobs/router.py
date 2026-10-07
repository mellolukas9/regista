"""Runs for the panel (docs/specs/design-system.md 7.3 and 7.4; `jobs` in the code).

Reads follow the client context (`auth.scoped()`); every write goes through `auth.writing()`,
which refuses "all clients". An id from another client is simply not found (404). What the agent
does with a run (take, start, finish, logs, screenshots) lives in `jobs/agent.py`.
"""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.permissions import Permission
from regista_api.core.errors import api_error
from regista_api.core.pagination import Pagination, like_pattern, order_by
from regista_api.jobs import logs, service
from regista_api.jobs.schemas import (
    ACTIVE_STATUSES,
    CreateJobRequest,
    JobDetail,
    JobItem,
    JobList,
    JobsSummary,
    JobStatus,
    Period,
)

router = APIRouter(tags=["jobs"])

_VIEW = Depends(Require(Permission.JOBS_VIEW))
_RUN = Depends(Require(Permission.JOBS_RUN))

_SORT = {
    "created_at": "j.created_at",
    "started_at": "j.started_at",
    "finished_at": "j.finished_at",
    "status": "j.status",
    "bot": "lower(b.name)",
}

_BRT = "America/Sao_Paulo"
_SINCE = {
    "today": (
        f"j.created_at >= date_trunc('day', now() AT TIME ZONE '{_BRT}') AT TIME ZONE '{_BRT}'"
    ),
    "7d": "j.created_at >= now() - interval '7 days'",
    "30d": "j.created_at >= now() - interval '30 days'",
    "all": "true",
}

_FROM = (
    "FROM jobs j"
    " JOIN bots b ON b.tenant_id = j.tenant_id AND b.id = j.bot_id"
    " JOIN pools p ON p.tenant_id = j.tenant_id AND p.id = j.pool_id"
    " JOIN tenants t ON t.id = j.tenant_id"
    " LEFT JOIN machines m ON m.tenant_id = j.tenant_id AND m.id = j.machine_id"
    " LEFT JOIN users u ON u.id = j.triggered_by"
    " LEFT JOIN bot_versions bv ON bv.tenant_id = j.tenant_id AND bv.id = j.bot_version_id"
)
# Staff (internal tenant) are not visible under a client's RLS, so a run they started is shown as
# theirs whichever way the row is read.
_COLUMNS = (
    "j.id, j.short_code, j.bot_id, b.name AS bot_name, j.status, j.trigger,"
    " CASE WHEN j.triggered_by IS NULL THEN NULL"
    "      WHEN u.tenant_id = j.tenant_id THEN u.email::text"
    "      ELSE 'Equipe Artemisys' END AS triggered_by,"
    " j.machine_id, m.name AS machine_name, j.pool_id, p.name AS pool_name, j.tenant_id,"
    " t.name AS client_name, j.created_at, j.assigned_at, j.started_at, j.finished_at,"
    " j.cancel_requested_at, j.error_code, j.error_message, j.error_reason,"
    " bv.version AS bot_version, j.items_successful,"
    " j.items_failed, j.items_abandoned, j.items_total"
)


def job_item(r: Row[Any]) -> JobItem:
    return JobItem(
        id=r.id,
        short_code=r.short_code,
        bot_id=r.bot_id,
        bot_name=r.bot_name,
        status=r.status,
        trigger=r.trigger,
        triggered_by=r.triggered_by,
        machine_id=r.machine_id,
        machine_name=r.machine_name,
        pool_id=r.pool_id,
        pool_name=r.pool_name,
        client_id=r.tenant_id,
        client_name=r.client_name,
        created_at=r.created_at,
        assigned_at=r.assigned_at,
        started_at=r.started_at,
        finished_at=r.finished_at,
        cancel_requested_at=r.cancel_requested_at,
        error_code=r.error_code,
        error_message=r.error_message,
        error_reason=r.error_reason,
        bot_version=r.bot_version,
        items_successful=r.items_successful,
        items_failed=r.items_failed,
        items_abandoned=r.items_abandoned,
        items_total=r.items_total,
    )


async def load_detail(db: AsyncSession, job_id: uuid.UUID) -> JobDetail:
    row = (
        await db.execute(
            text(
                f"SELECT {_COLUMNS}, b.package_name, j.params,"  # noqa: S608  (fixed fragments)
                " EXISTS (SELECT 1 FROM machines o WHERE o.tenant_id = j.tenant_id"
                "         AND o.pool_id = j.pool_id AND o.status = 'online') AS has_online"
                f" {_FROM} WHERE j.id = :id AND NOT t.is_internal"
            ),
            {"id": job_id},
        )
    ).first()
    if row is None:
        raise api_error(404, "job_not_found")
    return JobDetail(
        **job_item(row).model_dump(),
        package_name=row.package_name,
        params=row.params if isinstance(row.params, dict) else {},
        pool_has_online_machine=row.has_online,
    )


async def _require_version_in_production(auth: Auth, db: AsyncSession, bot_id: uuid.UUID) -> None:
    """In production only a signed version runs, so a bot without one in use could never start.
    Better to say so than to leave the run pending forever. Development may run a local folder."""
    if auth.state.settings.environment != "prod":
        return
    in_use = (
        await db.execute(text("SELECT current_version_id FROM bots WHERE id = :b"), {"b": bot_id})
    ).scalar_one_or_none()
    if in_use is not None:
        return
    published: bool = (
        await db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM bot_versions"
                " WHERE bot_id = :b AND status = 'published')"
            ),
            {"b": bot_id},
        )
    ).scalar_one()
    raise api_error(409, "bot_has_no_version_in_use" if published else "bot_has_no_version")


# --- create -----------------------------------------------------------------------------------


@router.post("/jobs", response_model=JobDetail, status_code=201)
async def create_job(body: CreateJobRequest, auth: Annotated[Auth, _RUN]) -> JobDetail:
    if len(json.dumps(body.params)) > 8192:
        raise api_error(422, "params_too_large")
    async with auth.writing() as db:
        bot = (
            await db.execute(
                text("SELECT id, pool_id, is_active FROM bots WHERE id = :b"), {"b": body.bot_id}
            )
        ).first()
        if bot is None:
            raise api_error(404, "bot_not_found")
        if not bot.is_active:
            raise api_error(409, "bot_inactive")
        await _require_version_in_production(auth, db, bot.id)
        job_id = await service.create_job(
            db,
            tenant_id=auth.client_id,
            bot_id=bot.id,
            pool_id=bot.pool_id,
            params=body.params,
            trigger="manual",
            triggered_by=auth.user.id,
        )
        await _audit(db, auth, "job.triggered", job_id, {"bot_id": str(bot.id)})
        return await load_detail(db, job_id)


# --- lists ------------------------------------------------------------------------------------


@router.get("/jobs", response_model=JobList)
async def list_jobs(
    auth: Annotated[Auth, _VIEW],
    paging: Pagination,
    bot_id: Annotated[uuid.UUID | None, Query()] = None,
    machine_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[JobStatus | None, Query()] = None,
    period: Annotated[Period, Query()] = "30d",
    q: Annotated[str | None, Query(max_length=40)] = None,
    sort: Annotated[str | None, Query(max_length=30)] = None,
) -> JobList:
    ordering = order_by(sort, _SORT, "-created_at").replace(", id ASC", ", j.id ASC")
    where = (
        f"NOT t.is_internal AND {_SINCE[period]}"
        " AND (CAST(:bot AS uuid) IS NULL OR j.bot_id = :bot)"
        " AND (CAST(:machine AS uuid) IS NULL OR j.machine_id = :machine)"
        " AND (CAST(:status AS text) IS NULL OR j.status = :status)"
        " AND (CAST(:q AS text) IS NULL OR j.short_code ILIKE :q ESCAPE '\\')"
    )
    params: dict[str, Any] = {
        "bot": bot_id,
        "machine": machine_id,
        "status": status,
        "q": like_pattern(q),
    }
    async with auth.scoped() as db:
        total: int = (
            await db.execute(text(f"SELECT count(*) {_FROM} WHERE {where}"), params)
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    f"SELECT {_COLUMNS} {_FROM} WHERE {where}"
                    f" ORDER BY {ordering} LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": paging.per_page, "offset": paging.offset},
            )
        ).all()
    return JobList(
        items=[job_item(r) for r in rows],
        total=total,
        page=paging.page,
        per_page=paging.per_page,
    )


# Declared before `/jobs/{job_id}` so "summary" is never read as an id.
@router.get("/jobs/summary", response_model=JobsSummary)
async def jobs_summary(auth: Annotated[Auth, _VIEW]) -> JobsSummary:
    async with auth.scoped() as db:
        row = (
            await db.execute(
                text(
                    "SELECT count(*) FILTER (WHERE j.status = 'pending') AS pending,"
                    " count(*) FILTER (WHERE j.status = ANY(:active)) AS active"
                    " FROM jobs j JOIN tenants t ON t.id = j.tenant_id WHERE NOT t.is_internal"
                ),
                {"active": list(ACTIVE_STATUSES)},
            )
        ).one()
    return JobsSummary(pending=row.pending, active=row.active)


@router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(job_id: uuid.UUID, auth: Annotated[Auth, _VIEW]) -> JobDetail:
    async with auth.scoped() as db:
        return await load_detail(db, job_id)


@router.get("/jobs/{job_id}/logs", response_model=logs.LogPage)
async def get_job_logs(
    job_id: uuid.UUID,
    auth: Annotated[Auth, _VIEW],
    after_seq: Annotated[int, Query(ge=0, le=9_999_999_999_999)] = 0,
    level: Annotated[logs.Level | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> logs.LogPage:
    """Lines in order. The panel asks again with the last `seq` it has to follow a live run. The
    text is untrusted: the panel shows it escaped, never as markup."""
    async with auth.scoped() as db:
        job = (
            await db.execute(
                text(
                    "SELECT j.tenant_id FROM jobs j JOIN tenants t ON t.id = j.tenant_id"
                    " WHERE j.id = :j AND NOT t.is_internal"
                ),
                {"j": job_id},
            )
        ).first()
        if job is None:
            raise api_error(404, "job_not_found")
        return await logs.read_logs(
            db,
            tenant_id=job.tenant_id,
            job_id=job_id,
            after_seq=after_seq,
            level=level,
            limit=limit,
        )


# --- cancel and rerun -------------------------------------------------------------------------


@router.post("/jobs/{job_id}/cancel", response_model=JobDetail)
async def cancel_job(job_id: uuid.UUID, auth: Annotated[Auth, _RUN]) -> JobDetail:
    """`pending` is cancelled at once. With a machine involved (`assigned`, `running`) the request
    is recorded and the agent learns of it on its next heartbeat: the robot stops, then the agent
    reports. Asking twice is harmless."""
    async with auth.writing() as db:
        job = (
            await db.execute(
                text("SELECT id, status, cancel_requested_at FROM jobs WHERE id = :j FOR UPDATE"),
                {"j": job_id},
            )
        ).first()
        if job is None:
            raise api_error(404, "job_not_found")
        if job.status == "pending":
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'cancelled', cancel_requested_at = now(),"
                    " finished_at = now(), updated_at = now() WHERE id = :j"
                ),
                {"j": job_id},
            )
            await _audit(db, auth, "job.cancelled", job_id, {"was": "pending"})
        elif job.status in ("assigned", "running"):
            if job.cancel_requested_at is None:
                await db.execute(
                    text(
                        "UPDATE jobs SET cancel_requested_at = now(), updated_at = now()"
                        " WHERE id = :j"
                    ),
                    {"j": job_id},
                )
                await _audit(db, auth, "job.cancel_requested", job_id, {"was": job.status})
        else:
            raise api_error(409, "job_not_active")
        return await load_detail(db, job_id)


@router.post("/jobs/{job_id}/rerun", response_model=JobDetail, status_code=201)
async def rerun_job(job_id: uuid.UUID, auth: Annotated[Auth, _RUN]) -> JobDetail:
    """A new run of the same bot with the same parameters, for a run that already ended."""
    async with auth.writing() as db:
        job = (
            await db.execute(
                text(
                    "SELECT j.status, j.bot_id, j.params, b.pool_id, b.is_active FROM jobs j"
                    " JOIN bots b ON b.tenant_id = j.tenant_id AND b.id = j.bot_id"
                    " WHERE j.id = :j"
                ),
                {"j": job_id},
            )
        ).first()
        if job is None:
            raise api_error(404, "job_not_found")
        if job.status in ACTIVE_STATUSES:
            raise api_error(409, "job_still_active")
        if not job.is_active:
            raise api_error(409, "bot_inactive")
        await _require_version_in_production(auth, db, job.bot_id)
        new_id = await service.create_job(
            db,
            tenant_id=auth.client_id,
            bot_id=job.bot_id,
            pool_id=job.pool_id,
            params=job.params if isinstance(job.params, dict) else {},
            trigger="manual",
            triggered_by=auth.user.id,
        )
        await _audit(db, auth, "job.triggered", new_id, {"rerun_of": str(job_id)})
        return await load_detail(db, new_id)


async def _audit(
    db: AsyncSession, auth: Auth, action: str, job_id: uuid.UUID, metadata: dict[str, object]
) -> None:
    await audit.record(
        db,
        tenant_id=auth.client_id,
        actor_type="user",
        actor_id=auth.user.id,
        action=action,
        target_type="job",
        target_id=job_id,
        metadata=metadata,
        ip=auth.ip,
    )
