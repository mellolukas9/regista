"""The background worker: "Sem sinal" sweep, rate limit purge and running with several workers."""

import asyncio
import os
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_api.machines import presence
from regista_api.tasks.app import SWEEP_TASK, build_app, psycopg_conninfo

from .conftest import DbUrls, Seed, make_settings

Factory = async_sessionmaker[AsyncSession]

# The test database is shared by the whole session, so the sweeps here use a one-hour threshold
# and silent machines are two hours old: nothing another test created a moment ago is touched.
THRESHOLD = 3600


def test_the_job_queue_uses_the_api_database_with_psycopg() -> None:
    assert (
        psycopg_conninfo("postgresql+asyncpg://regista_app:pw@db:5432/regista")
        == "postgresql://regista_app:pw@db:5432/regista"
    )
    assert psycopg_conninfo("postgresql://u:p@h/d") == "postgresql://u:p@h/d"


async def _machine(
    factory: Factory,
    tenant_id: uuid.UUID,
    *,
    status: str = "online",
    silent: timedelta = timedelta(hours=2),
) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        pool_id: uuid.UUID = (
            await db.execute(
                text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n) RETURNING id"),
                {"t": tenant_id, "n": f"pool-{suffix}"},
            )
        ).scalar_one()
        machine_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name, public_key, status,"
                    " credential_version, last_seen_at, enrolled_at)"
                    " VALUES (:t, :p, :n, :k, :s, 1, now() - CAST(:silent AS interval), now())"
                    " RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "p": pool_id,
                    "n": f"m-{suffix}",
                    "k": os.urandom(32),
                    "s": status,
                    "silent": silent,
                },
            )
        ).scalar_one()
    return machine_id


async def _pending(factory: Factory, tenant_id: uuid.UUID) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        pool_id: uuid.UUID = (
            await db.execute(
                text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n) RETURNING id"),
                {"t": tenant_id, "n": f"pool-{suffix}"},
            )
        ).scalar_one()
        machine_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name) VALUES (:t, :p, :n)"
                    " RETURNING id"
                ),
                {"t": tenant_id, "p": pool_id, "n": f"m-{suffix}"},
            )
        ).scalar_one()
    return machine_id


async def _state(
    owner: Factory, tenant_id: uuid.UUID, machine_id: uuid.UUID
) -> tuple[str, list[str]]:
    async with tenant_session(owner, tenant_id=tenant_id) as db:
        status: str = (
            await db.execute(text("SELECT status FROM machines WHERE id = :m"), {"m": machine_id})
        ).scalar_one()
        events = [
            r[0]
            for r in await db.execute(
                text(
                    "SELECT kind FROM machine_events WHERE machine_id = :m ORDER BY created_at, id"
                ),
                {"m": machine_id},
            )
        ]
    return status, events


async def _snapshot(owner: Factory, tenant_id: uuid.UUID) -> list[str]:
    async with tenant_session(owner, tenant_id=tenant_id) as db:
        rows = await db.execute(
            text(
                "SELECT row_to_json(m)::text FROM machines m"
                " UNION ALL SELECT row_to_json(e)::text FROM machine_events e ORDER BY 1"
            )
        )
        return [r[0] for r in rows]


# --- the sweep --------------------------------------------------------------------------------


async def test_only_silent_online_machines_go_offline_and_each_gets_one_event(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    a, b = seed.tenant_a, seed.tenant_b
    silent_a = await _machine(app_factory, a)
    silent_b = await _machine(app_factory, b)
    recent_a = await _machine(app_factory, a, silent=timedelta(minutes=30))
    already_offline = await _machine(app_factory, a, status="offline")
    pending = await _pending(app_factory, a)

    changed = await presence.mark_stale_machines_offline(
        app_factory, offline_after_seconds=THRESHOLD
    )
    assert changed >= 2

    assert await _state(owner_factory, a, silent_a) == ("offline", ["went_offline"])
    assert await _state(owner_factory, b, silent_b) == ("offline", ["went_offline"])
    assert await _state(owner_factory, a, recent_a) == ("online", [])
    assert await _state(owner_factory, a, already_offline) == ("offline", [])
    assert await _state(owner_factory, a, pending) == ("pending", [])

    # The events belong to the machine's own client.
    async with tenant_session(owner_factory, tenant_id=b) as db:
        owners: list[uuid.UUID] = list(
            (
                await db.execute(
                    text("SELECT tenant_id FROM machine_events WHERE machine_id = :m"),
                    {"m": silent_b},
                )
            )
            .scalars()
            .all()
        )
    assert list(owners) == [b]

    # Idempotent: a second sweep finds nothing of ours and adds no event.
    await presence.mark_stale_machines_offline(app_factory, offline_after_seconds=THRESHOLD)
    assert await _state(owner_factory, a, silent_a) == ("offline", ["went_offline"])
    assert await _state(owner_factory, b, silent_b) == ("offline", ["went_offline"])


async def test_a_sweep_that_only_finds_machines_of_a_changes_nothing_of_b(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    await _machine(app_factory, seed.tenant_b, silent=timedelta(minutes=10))  # not silent enough
    await _machine(app_factory, seed.tenant_b, status="offline")
    silent_a = await _machine(app_factory, seed.tenant_a)
    before = await _snapshot(owner_factory, seed.tenant_b)

    await presence.mark_stale_machines_offline(app_factory, offline_after_seconds=THRESHOLD)

    assert (await _state(owner_factory, seed.tenant_a, silent_a))[0] == "offline"
    assert await _snapshot(owner_factory, seed.tenant_b) == before


async def test_a_machine_that_spoke_meanwhile_is_left_alone(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    """The write repeats the silence condition, so a heartbeat between the read and the write
    wins: no change and no event."""
    machine = await _machine(app_factory, seed.tenant_a)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as db:
        await db.execute(
            text("UPDATE machines SET last_seen_at = now() WHERE id = :m"), {"m": machine}
        )
    await presence.mark_stale_machines_offline(app_factory, offline_after_seconds=THRESHOLD)
    assert await _state(owner_factory, seed.tenant_a, machine) == ("online", [])


# --- purge ------------------------------------------------------------------------------------


async def test_the_purge_removes_only_old_windows(
    app_factory: Factory, owner_factory: Factory
) -> None:
    old_key, fresh_key = os.urandom(32), os.urandom(32)
    async with tenant_session(owner_factory, platform_admin=True) as db:
        await db.execute(text("SELECT set_config('app.rate_limit', 'on', true)"))
        await db.execute(
            text(
                "INSERT INTO auth_rate_limits (key_hash, window_start, count)"
                " VALUES (:o, now() - interval '3 days', 1), (:f, now(), 1)"
            ),
            {"o": old_key, "f": fresh_key},
        )
    removed = await presence.purge_auth_rate_limits(app_factory, older_than_seconds=86_400)
    assert removed >= 1
    async with tenant_session(owner_factory, platform_admin=True) as db:
        await db.execute(text("SELECT set_config('app.rate_limit', 'on', true)"))
        left = {
            bytes(r[0])
            for r in await db.execute(
                text("SELECT key_hash FROM auth_rate_limits WHERE key_hash IN (:o, :f)"),
                {"o": old_key, "f": fresh_key},
            )
        }
    assert left == {fresh_key}


# --- several workers --------------------------------------------------------------------------


async def _wait_for(check: object, seconds: float) -> bool:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if await check():  # type: ignore[operator]
            return True
        await asyncio.sleep(0.25)
    return False


async def test_two_workers_run_each_tick_once(
    db_urls: DbUrls, app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    """Two workers (two apps, two connections, like two processes) share one queue. Every tick of
    the periodic sweep becomes one job, whichever worker takes it, and a machine that went silent
    gets exactly one `went_offline`."""
    settings = make_settings(
        db_urls, machine_offline_after_seconds=THRESHOLD, machine_sweep_cron="* * * * * *"
    )
    first, second = build_app(settings, app_factory), build_app(settings, app_factory)
    silent_early = await _machine(app_factory, seed.tenant_a)
    async with tenant_session(owner_factory) as db:
        jobs_before: int = (
            await db.execute(text("SELECT coalesce(max(id), 0) FROM procrastinate_jobs"))
        ).scalar_one()

    silent_late: uuid.UUID | None = None
    async with first.open_async(), second.open_async():
        workers = [
            asyncio.create_task(app.run_worker_async(name=name, install_signal_handlers=False))
            for app, name in ((first, "worker-1"), (second, "worker-2"))
        ]
        try:

            async def early_done() -> bool:
                return (await _state(owner_factory, seed.tenant_a, silent_early))[0] == "offline"

            assert await _wait_for(early_done, 10), "the first sweep never ran"

            # A machine that goes silent while the workers are already running is picked up too.
            silent_late = await _machine(app_factory, seed.tenant_b)

            async def late_done() -> bool:
                assert silent_late is not None
                return (await _state(owner_factory, seed.tenant_b, silent_late))[0] == "offline"

            assert await _wait_for(late_done, 10), "a later tick never ran"
            await asyncio.sleep(2)  # a few more ticks, to give a duplicate the chance to appear
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    assert silent_late is not None
    assert await _state(owner_factory, seed.tenant_a, silent_early) == ("offline", ["went_offline"])
    assert await _state(owner_factory, seed.tenant_b, silent_late) == ("offline", ["went_offline"])

    async with tenant_session(owner_factory) as db:
        # A periodic job's `timestamp` argument is the tick it belongs to. (The periodic table
        # keeps only the latest tick per task, so it cannot be used to count them.)
        jobs = (
            await db.execute(
                text(
                    "SELECT count(*) AS jobs, count(DISTINCT args->>'timestamp') AS ticks"
                    " FROM procrastinate_jobs WHERE task_name = :t AND id > :b"
                ),
                {"t": SWEEP_TASK, "b": jobs_before},
            )
        ).one()
    assert jobs.jobs >= 3, "the sweep should have ticked several times"
    assert jobs.ticks == jobs.jobs, "two jobs for the same tick: a tick ran twice"

    # Jobs of the sweep finished without failing.
    async with tenant_session(owner_factory) as db:
        failed: int = (
            await db.execute(
                text(
                    "SELECT count(*) FROM procrastinate_jobs"
                    " WHERE task_name = :t AND id > :b AND status = 'failed'"
                ),
                {"t": SWEEP_TASK, "b": jobs_before},
            )
        ).scalar_one()
    assert failed == 0
