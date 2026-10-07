"""What the agent does with runs: take one, start it, finish it (docs/specs/agent.md, protocol).

Everything the agent sends is untrusted data: fixed schemas, size limits, secrets cut out before
anything is stored. The tenant comes from the machine's signed token, never from a parameter, and
every change repeats `machine_id = this machine`, so a machine can only touch its own runs. A run
that belongs to someone else is simply not found (404), like one that does not exist.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.machine import MachineAuth, MachineRoute
from regista_api.core.errors import api_error
from regista_api.core.redact import clean_line
from regista_api.jobs import logs, service
from regista_api.jobs.waiters import JobWaiters, TooManyWaiters
from regista_pkg import REASONS

router = APIRouter(prefix="/agent", tags=["agent"])

# Codes an agent may report. `machine_lost` and `machine_revoked` are decided by the server.
AgentErrorCode = Literal[
    "robot_failed",
    "robot_not_found",
    "timeout",
    "cancelled",
    "internal",
    "package_invalid",
    "robot_not_allowed",
    "runtime_missing",
    "environment_failed",
]
# Codes whose free message is not kept: the panel shows a fixed text per code (design-system 15).
_FIXED_TEXT_CODES = frozenset(
    {"package_invalid", "robot_not_allowed", "runtime_missing", "environment_failed"}
)
_SAFETY_TICK_SECONDS = 10


class JobAssignment(BaseModel):
    job_id: uuid.UUID
    short_code: str
    # A folder name in development, a package from M4 on. Never a command.
    package_name: str
    params: dict[str, object]
    timeout_seconds: int
    # The version in use when this machine took the run (null only in development, where the
    # robot comes from a local folder). The agent fetches the package by this id.
    bot_version_id: uuid.UUID | None = None
    version: str | None = None


class JobAck(BaseModel):
    status: str
    # The panel asked to cancel this run: stop the robot and report `cancelled`.
    cancel_requested: bool


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{}]})


class FailRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"error_code": "robot_failed", "message": "O robô caiu."}]},
    )
    error_code: AgentErrorCode
    message: Annotated[str, Field(max_length=2000)] = ""
    # Only for `package_invalid`: why, from a closed list. Anything else is dropped by the server.
    reason: Annotated[str | None, Field(max_length=40)] = None


# Takes the oldest pending run of this machine's pool, atomically. The machine row is locked so
# two requests of the same machine are serialised; SKIP LOCKED lets other machines pick other runs
# without waiting; the unique index on active runs per machine is the last line of defence.
_TAKE = text(
    "WITH me AS (SELECT id, pool_id FROM machines"
    "            WHERE id = :m AND tenant_id = :t AND status = 'online' FOR UPDATE),"
    " picked AS ("
    "  SELECT j.id FROM jobs j JOIN me ON j.pool_id = me.pool_id"
    "  WHERE j.tenant_id = :t AND j.status = 'pending' AND j.cancel_requested_at IS NULL"
    "    AND NOT EXISTS (SELECT 1 FROM jobs a WHERE a.tenant_id = :t AND a.machine_id = :m"
    "                    AND a.status IN ('assigned', 'running'))"
    "    AND (NOT :prod OR EXISTS (SELECT 1 FROM bots b WHERE b.tenant_id = j.tenant_id"
    "                              AND b.id = j.bot_id AND b.current_version_id IS NOT NULL))"
    "  ORDER BY j.created_at, j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1)"
    " UPDATE jobs SET status = 'assigned', machine_id = :m, assigned_at = now(),"
    "  updated_at = now(),"
    "  bot_version_id = (SELECT b.current_version_id FROM bots b"
    "                    WHERE b.tenant_id = jobs.tenant_id AND b.id = jobs.bot_id)"
    " WHERE id IN (SELECT id FROM picked)"
    " RETURNING id, short_code, bot_id, params, bot_version_id"
)


async def _take(machine: MachineAuth) -> JobAssignment | None:
    async with machine.session() as db:
        # In production only a bot with a version in use is taken: nothing runs unsigned.
        prod = machine.state.settings.environment == "prod"
        row = (
            await db.execute(_TAKE, {"m": machine.machine_id, "t": machine.tenant_id, "prod": prod})
        ).first()
        if row is None:
            return None
        package: str = (
            await db.execute(text("SELECT package_name FROM bots WHERE id = :b"), {"b": row.bot_id})
        ).scalar_one()
        version: str | None = None
        if row.bot_version_id is not None:
            version = (
                await db.execute(
                    text("SELECT version FROM bot_versions WHERE id = :v"),
                    {"v": row.bot_version_id},
                )
            ).scalar_one()
        await audit.record(
            db,
            tenant_id=machine.tenant_id,
            actor_type="machine",
            actor_id=machine.machine_id,
            action="job.assigned",
            target_type="job",
            target_id=row.id,
            ip=machine.ip,
        )
    return JobAssignment(
        job_id=row.id,
        short_code=row.short_code,
        package_name=package,
        params=row.params if isinstance(row.params, dict) else {},
        timeout_seconds=machine.state.settings.job_timeout_seconds,
        bot_version_id=row.bot_version_id,
        version=version,
    )


@router.get(
    "/jobs/next",
    response_model=JobAssignment,
    responses={204: {"description": "Nothing to run right now; ask again."}},
)
async def next_job(
    request: Request,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
    wait: Annotated[int, Query(ge=0, le=30)] = 30,
) -> Response | JobAssignment:
    """Long-polling. Waits up to `wait` seconds (never more than the server's own limit) without
    holding a database connection, and answers 204 when nothing came."""
    waiters: JobWaiters = request.app.state.job_waiters
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(wait, machine.state.settings.agent_poll_wait_seconds)

    async with machine.session() as db:
        pool_id: uuid.UUID | None = (
            await db.execute(
                text("SELECT pool_id FROM machines WHERE id = :m"), {"m": machine.machine_id}
            )
        ).scalar_one_or_none()
    if pool_id is None:
        raise api_error(401, "not_authenticated")

    try:
        with waiters.register(machine.tenant_id, pool_id) as event:
            while True:
                # Cleared before looking, so a notification that lands while the query runs is
                # still seen by the wait below.
                event.clear()
                job = await _take(machine)
                if job is not None:
                    return job
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return Response(status_code=204)
                try:
                    await asyncio.wait_for(
                        event.wait(), timeout=min(remaining, _SAFETY_TICK_SECONDS)
                    )
                except TimeoutError:
                    pass
    except TooManyWaiters:
        raise HTTPException(
            status_code=503,
            detail={"code": "too_many_waiters"},
            headers={"Retry-After": "5"},
        ) from None


@router.post("/jobs/{job_id}/start", response_model=JobAck)
async def start_job(
    job_id: uuid.UUID,
    body: EmptyRequest,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> JobAck:
    async with machine.session() as db:
        row = (
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'running', started_at = now(), updated_at = now()"
                    " WHERE id = :j AND machine_id = :m AND status = 'assigned'"
                    " RETURNING status, cancel_requested_at"
                ),
                {"j": job_id, "m": machine.machine_id},
            )
        ).first()
        if row is None:
            raise await _why_not(machine, db, job_id)
    return JobAck(status=row.status, cancel_requested=row.cancel_requested_at is not None)


@router.post("/jobs/{job_id}/complete", response_model=JobAck)
async def complete_job(
    job_id: uuid.UUID,
    body: EmptyRequest,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> JobAck:
    """The robot ended well. If a cancellation was asked meanwhile but the robot had already
    finished, the real result stands."""
    async with machine.session() as db:
        done = await service.finish_job(
            db,
            job_id=job_id,
            from_statuses=("running",),
            status="completed",
            machine_id=machine.machine_id,
        )
        if not done:
            raise await _why_not(machine, db, job_id)
    return JobAck(status="completed", cancel_requested=False)


@router.post("/jobs/{job_id}/fail", response_model=JobAck)
async def fail_job(
    job_id: uuid.UUID,
    body: FailRequest,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> JobAck:
    """`cancelled` is only believed when the panel did ask for it. Everything else is a failure,
    with the message scrubbed and cut: it came from the robot's own output."""
    message = clean_line(body.message, max_bytes=1000)
    reason: str | None = None
    if body.error_code in _FIXED_TEXT_CODES:
        message = ""  # the free text only travels in the run's logs
    if body.error_code == "package_invalid" and body.reason in REASONS:
        reason = body.reason
    async with machine.session() as db:
        status = "failed"
        code: str | None = body.error_code
        if body.error_code == "cancelled":
            asked = (
                await db.execute(
                    text(
                        "SELECT cancel_requested_at IS NOT NULL FROM jobs"
                        " WHERE id = :j AND machine_id = :m"
                    ),
                    {"j": job_id, "m": machine.machine_id},
                )
            ).scalar_one_or_none()
            if asked is None:
                raise api_error(404, "job_not_found")
            if not asked:
                raise api_error(409, "cancel_not_requested")
            status, code, message = "cancelled", None, ""
        done = await service.finish_job(
            db,
            job_id=job_id,
            from_statuses=("assigned", "running"),
            status=status,
            error_code=code,
            error_message=message,
            error_reason=reason,
            machine_id=machine.machine_id,
        )
        if not done:
            raise await _why_not(machine, db, job_id)
        await audit.record(
            db,
            tenant_id=machine.tenant_id,
            actor_type="machine",
            actor_id=machine.machine_id,
            action=f"job.{status}",
            target_type="job",
            target_id=job_id,
            metadata={"error_code": code, "error_reason": reason},
            ip=machine.ip,
        )
    return JobAck(status=status, cancel_requested=False)


@router.post("/logs", response_model=logs.LogBatchResult)
async def send_logs(
    body: logs.LogBatch, machine: Annotated[MachineAuth, Depends(MachineRoute())]
) -> logs.LogBatchResult:
    """A batch of log lines of a run of this machine. Re-sending a batch is harmless: a line that
    is already stored (same job, `seq` and time) is not stored twice."""
    async with machine.session() as db:
        return await logs.store_batch(
            db,
            settings=machine.state.settings,
            tenant_id=machine.tenant_id,
            machine_id=machine.machine_id,
            batch=body,
            now=datetime.now(UTC),
        )


async def _why_not(machine: MachineAuth, db: AsyncSession, job_id: uuid.UUID) -> HTTPException:
    """Nothing matched: either the run is not this machine's (404, same as unknown) or it is not
    in a state that allows the change (409 with where it stands)."""
    row = (
        await db.execute(
            text("SELECT status FROM jobs WHERE id = :j AND machine_id = :m"),
            {"j": job_id, "m": machine.machine_id},
        )
    ).first()
    if row is None:
        return api_error(404, "job_not_found")
    return api_error(409, "job_not_active", status=row.status)
