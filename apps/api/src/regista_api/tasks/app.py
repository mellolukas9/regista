"""The background job queue: Procrastinate on PostgreSQL (ADR 0002).

The app is built from settings, not at import time, so every test can build its own with its own
cron. It connects as `regista_app` (no BYPASSRLS) like the API; the job tables hold platform data
only and task arguments carry ids, never content of a client.

Periodic tasks do not run twice with several workers: Procrastinate keeps the latest tick of each
task in `procrastinate_periodic_defers` and drops a tick that is not newer, `queueing_lock` keeps
a slow run from piling up copies, `lock` serialises runs, and the tasks themselves are idempotent.
"""

import re

import structlog
from procrastinate import App, PsycopgConnector, builtin_tasks
from procrastinate.job_context import JobContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.config import Settings
from regista_api.machines import presence

log = structlog.get_logger()

SWEEP_TASK = "regista.mark_stale_machines_offline"
PURGE_TASK = "regista.purge_auth_rate_limits"
REMOVE_OLD_JOBS_TASK = "regista.remove_old_jobs"

# Finished jobs are kept for a week (enough to investigate), then removed every night.
_JOB_RETENTION_HOURS = 24 * 7


def psycopg_conninfo(database_url: str) -> str:
    """The SQLAlchemy URL of the API (`postgresql+asyncpg://...`) as a libpq URI."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)


def build_app(settings: Settings, factory: async_sessionmaker[AsyncSession]) -> App:
    # `conninfo` goes to the connection pool (not inside `kwargs`, which are extra arguments for
    # every connection and would collide with it).
    app = App(connector=PsycopgConnector(conninfo=psycopg_conninfo(settings.database_url)))

    @app.periodic(cron=settings.machine_sweep_cron)
    @app.task(name=SWEEP_TASK, queueing_lock=SWEEP_TASK, lock=SWEEP_TASK)
    async def mark_stale_machines_offline(timestamp: int) -> None:
        changed = await presence.mark_stale_machines_offline(
            factory, offline_after_seconds=settings.machine_offline_after_seconds
        )
        if changed:
            log.info("machines_marked_offline", count=changed)

    @app.periodic(cron="0 * * * * 0")  # every hour, on the hour
    @app.task(name=PURGE_TASK, queueing_lock=PURGE_TASK, lock=PURGE_TASK)
    async def purge_auth_rate_limits(timestamp: int) -> None:
        removed = await presence.purge_auth_rate_limits(
            factory, older_than_seconds=settings.rate_limit_retention_seconds
        )
        if removed:
            log.info("rate_limit_windows_purged", count=removed)

    @app.periodic(cron="0 3 * * * 0")  # every night at 03:00
    @app.task(
        name=REMOVE_OLD_JOBS_TASK,
        queueing_lock=REMOVE_OLD_JOBS_TASK,
        lock=REMOVE_OLD_JOBS_TASK,
        pass_context=True,
    )
    async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
        await builtin_tasks.remove_old_jobs(
            context,
            max_hours=_JOB_RETENTION_HOURS,
            remove_failed=True,
            remove_cancelled=True,
            remove_aborted=True,
        )

    return app
