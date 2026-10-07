"""Run logs: what the agent sends and what the panel reads (docs/specs/orchestration.md).

Every line comes from a robot's own output, so it is untrusted data: a fixed schema, a size limit
per line and per run, secrets and terminal control sequences cut out before storage (the agent does
it too; this is the net under it), and the panel shows it escaped.

`job_logs` has one partition per month. If the partition of a month is missing the write fails
loudly (an ERROR in the log and a 503 the agent retries): a line is never dropped in silence.
"""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.core.config import Settings
from regista_api.core.errors import api_error
from regista_api.core.redact import clean_line

log = structlog.get_logger()

Level = Literal["INFO", "WARN", "ERROR"]
# A line of the agent's own, written when a run goes over its log budget. A fixed `seq` makes
# the "ON CONFLICT DO NOTHING" write it once.
LIMIT_SEQ = 9_000_000_000_000
LIMIT_MESSAGE = "Limite de logs desta execução atingido. As próximas linhas não são guardadas."
# After a run ends the agent may still flush its last lines for this long.
GRACE = timedelta(minutes=5)


class LogLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: Annotated[int, Field(ge=0, le=1_000_000_000_000)]
    ts: datetime
    level: Level
    message: Annotated[str, Field(max_length=8192)]


class LogBatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "job_id": "00000000-0000-4000-8000-000000000000",
                    "lines": [
                        {
                            "seq": 1,
                            "ts": "2026-10-03T12:00:00Z",
                            "level": "INFO",
                            "message": "Abrindo o navegador",
                        }
                    ],
                }
            ]
        },
    )
    job_id: uuid.UUID
    lines: Annotated[list[LogLineIn], Field(min_length=1, max_length=200)]


class LogBatchResult(BaseModel):
    accepted: int
    # True once the run reached its log budget: the agent may stop sending.
    truncated: bool


class LogLine(BaseModel):
    seq: int
    ts: datetime
    level: Level
    message: str
    item_ref: str | None
    attempt: int | None


class LogPage(BaseModel):
    items: list[LogLine]
    # Lines of the whole run per level, for the filter counts (not only this page).
    level_counts: dict[str, int]
    # True when there are more lines after the last one returned.
    has_more: bool


_BUDGET = text(
    "SELECT count(*) AS lines, coalesce(sum(octet_length(message)), 0) AS bytes"
    " FROM job_logs WHERE tenant_id = :t AND job_id = :j"
)
_INSERT = text(
    "INSERT INTO job_logs (tenant_id, job_id, seq, ts, level, message)"
    " VALUES (:t, :j, :seq, :ts, :level, :message)"
    " ON CONFLICT (job_id, seq, ts) DO NOTHING"
)


def _is_missing_partition(exc: DBAPIError) -> bool:
    return "no partition of relation" in str(exc.orig)


async def store_batch(
    db: AsyncSession,
    *,
    settings: Settings,
    tenant_id: uuid.UUID,
    machine_id: uuid.UUID,
    batch: LogBatch,
    now: datetime,
) -> LogBatchResult:
    """Store a batch for a run of this machine. Raises 404 for a run that is not this machine's
    (same answer as for one that does not exist) and 409 once the run closed long ago."""
    job = (
        await db.execute(
            text(
                "SELECT status, created_at, finished_at FROM jobs WHERE id = :j AND machine_id = :m"
            ),
            {"j": batch.job_id, "m": machine_id},
        )
    ).first()
    if job is None:
        raise api_error(404, "job_not_found")
    if job.finished_at is not None and now - job.finished_at > GRACE:
        raise api_error(409, "job_closed")

    # The agent's clock is not trusted: a timestamp outside the life of the run (a little slack
    # on both sides) is pulled to the nearest edge instead of being refused.
    lowest = job.created_at - timedelta(minutes=1)
    highest = now + timedelta(minutes=1)

    used = (await db.execute(_BUDGET, {"t": tenant_id, "j": batch.job_id})).one()
    lines_left = settings.log_job_max_lines - used.lines
    bytes_left = settings.log_job_max_bytes - used.bytes

    rows: list[dict[str, object]] = []
    stamps: list[datetime] = []
    truncated = lines_left <= 0 or bytes_left <= 0
    for line in batch.lines:
        message = clean_line(line.message, max_bytes=settings.log_line_max_bytes)
        size = len(message.encode("utf-8"))
        if lines_left <= 0 or bytes_left < size:
            truncated = True
            break
        lines_left -= 1
        bytes_left -= size
        stamp = min(max(line.ts, lowest), highest)
        stamps.append(stamp)
        rows.append(
            {
                "t": tenant_id,
                "j": batch.job_id,
                "seq": line.seq,
                "ts": stamp,
                "level": line.level,
                "message": message,
            }
        )
    if truncated:
        rows.append(
            {
                "t": tenant_id,
                "j": batch.job_id,
                "seq": LIMIT_SEQ,
                "ts": min(max(job.created_at, lowest), highest),
                "level": "WARN",
                "message": LIMIT_MESSAGE,
            }
        )
    try:
        # One statement for the batch; the savepoint keeps a failure from poisoning the caller.
        async with db.begin_nested():
            await db.execute(_INSERT, rows)
    except DBAPIError as exc:
        if not _is_missing_partition(exc):
            raise
        months = sorted({f"{stamp:%Y-%m}" for stamp in stamps} or {f"{now:%Y-%m}"})
        log.error(
            "job_logs_partition_missing",
            months=months,
            job_id=str(batch.job_id),
            hint="run the worker (it creates the partitions) or app.ensure_job_log_partitions(3)",
        )
        raise api_error(503, "log_partition_missing", months=months) from None
    return LogBatchResult(accepted=len(rows) - (1 if truncated else 0), truncated=truncated)


async def read_logs(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    after_seq: int,
    level: Level | None,
    limit: int,
) -> LogPage:
    rows = (
        await db.execute(
            text(
                "SELECT seq, ts, level, message, item_ref, attempt FROM job_logs"
                " WHERE tenant_id = :t AND job_id = :j AND seq > :after"
                " AND (CAST(:level AS text) IS NULL OR level = :level)"
                " ORDER BY seq LIMIT :limit"
            ),
            {"t": tenant_id, "j": job_id, "after": after_seq, "level": level, "limit": limit + 1},
        )
    ).all()
    counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
    for r in (
        await db.execute(
            text(
                "SELECT level, count(*) AS n FROM job_logs"
                " WHERE tenant_id = :t AND job_id = :j GROUP BY level"
            ),
            {"t": tenant_id, "j": job_id},
        )
    ).all():
        counts[r.level] = r.n
    page = rows[:limit]
    return LogPage(
        items=[
            LogLine(
                seq=r.seq,
                ts=r.ts,
                level=r.level,
                message=r.message,
                item_ref=r.item_ref,
                attempt=r.attempt,
            )
            for r in page
        ],
        level_counts=counts,
        has_more=len(rows) > limit,
    )
