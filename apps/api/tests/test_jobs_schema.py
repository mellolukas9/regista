"""Database-level guarantees of bots, jobs, job_logs and artifacts (M3, ADR 0020)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import DbUrls, Seed

Factory = async_sessionmaker[AsyncSession]

TABLES = ("bots", "jobs", "job_logs", "artifacts")


def _name() -> str:
    return f"x-{uuid.uuid4().hex[:10]}"


def _code() -> str:
    return f"exec-{uuid.uuid4().hex[:6]}"


async def _pool(s: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    r = await s.execute(
        text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n) RETURNING id"),
        {"t": tenant_id, "n": _name()},
    )
    return r.scalar_one()


async def _machine(s: AsyncSession, tenant_id: uuid.UUID, pool_id: uuid.UUID) -> uuid.UUID:
    r = await s.execute(
        text("INSERT INTO machines (tenant_id, pool_id, name) VALUES (:t, :p, :n) RETURNING id"),
        {"t": tenant_id, "p": pool_id, "n": _name()},
    )
    return r.scalar_one()


async def _bot(s: AsyncSession, tenant_id: uuid.UUID, pool_id: uuid.UUID) -> uuid.UUID:
    r = await s.execute(
        text(
            "INSERT INTO bots (tenant_id, pool_id, name, package_name)"
            " VALUES (:t, :p, :n, :k) RETURNING id"
        ),
        {"t": tenant_id, "p": pool_id, "n": _name(), "k": "demo_" + uuid.uuid4().hex[:8]},
    )
    return r.scalar_one()


async def _job(
    s: AsyncSession,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    pool_id: uuid.UUID,
    **fields: object,
) -> uuid.UUID:
    cols = {"tenant_id": tenant_id, "bot_id": bot_id, "pool_id": pool_id, "short_code": _code()}
    cols.update(fields)
    names = ", ".join(cols)
    marks = ", ".join(f":{c}" for c in cols)
    r = await s.execute(
        text(f"INSERT INTO jobs ({names}) VALUES ({marks}) RETURNING id"),  # noqa: S608
        cols,
    )
    return r.scalar_one()


async def _log(
    s: AsyncSession, tenant_id: uuid.UUID, job_id: uuid.UUID, seq: int, ts: datetime | None = None
) -> None:
    await s.execute(
        text(
            "INSERT INTO job_logs (tenant_id, job_id, seq, ts, level, message)"
            " VALUES (:t, :j, :q, :ts, 'INFO', 'hello')"
        ),
        {"t": tenant_id, "j": job_id, "q": seq, "ts": ts or datetime.now(UTC)},
    )


async def _world(factory: Factory, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    async with tenant_session(factory, tenant_id=tenant_id) as s:
        pool = await _pool(s, tenant_id)
        machine = await _machine(s, tenant_id, pool)
        bot = await _bot(s, tenant_id, pool)
        job = await _job(s, tenant_id, bot, pool)
        await _log(s, tenant_id, job, 1)
        artifact = (
            await s.execute(
                text(
                    "INSERT INTO artifacts (tenant_id, job_id, storage_key, content_type,"
                    " size_bytes) VALUES (:t, :j, :k, 'image/png', 10) RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "j": job,
                    "k": f"tenants/{tenant_id}/jobs/{job}/{uuid.uuid4()}.png",
                },
            )
        ).scalar_one()
    return {"pool": pool, "machine": machine, "bot": bot, "job": job, "artifact": artifact}


# --- isolation ---------------------------------------------------------------------------------


async def test_each_tenant_sees_only_its_own_rows(app_factory: Factory, seed: Seed) -> None:
    await _world(app_factory, seed.tenant_a)
    await _world(app_factory, seed.tenant_b)
    for table in TABLES:
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            foreign = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id <> :t"),  # noqa: S608
                    {"t": seed.tenant_a},
                )
            ).scalar_one()
            own = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
        assert foreign == 0, table
        assert own > 0, table
    async with tenant_session(app_factory) as s:
        for table in TABLES:
            count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, f"{table} visible without a tenant"


async def test_a_platform_admin_reads_everything_but_writes_nothing_elsewhere(
    app_factory: Factory, seed: Seed
) -> None:
    w = await _world(app_factory, seed.tenant_b)
    async with tenant_session(app_factory, platform_admin=True) as s:
        assert (
            await s.execute(text("SELECT count(*) FROM jobs WHERE id = :j"), {"j": w["job"]})
        ).scalar_one() == 1
        updated = await s.execute(
            text("UPDATE jobs SET error_message = 'x' WHERE id = :j"), {"j": w["job"]}
        )
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError):
            await _job(s, seed.tenant_b, w["bot"], w["pool"])


async def test_a_row_cannot_point_at_a_parent_of_another_tenant(
    app_factory: Factory, seed: Seed
) -> None:
    a = await _world(app_factory, seed.tenant_a)
    b = await _world(app_factory, seed.tenant_b)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="fk_jobs_tenant_bot_bots"):
            await _job(s, seed.tenant_a, b["bot"], a["pool"])
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="fk_jobs_tenant_machine_machines"):
            await _job(
                s, seed.tenant_a, a["bot"], a["pool"], status="running", machine_id=b["machine"]
            )
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="fk_bots_tenant_pool"):
            await s.execute(
                text(
                    "INSERT INTO bots (tenant_id, pool_id, name, package_name)"
                    " VALUES (:t, :p, 'n', 'pkg')"
                ),
                {"t": seed.tenant_a, "p": b["pool"]},
            )
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="fk_job_logs_tenant_job_jobs"):
            await _log(s, seed.tenant_a, b["job"], 1)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="fk_artifacts_tenant_job_jobs"):
            await s.execute(
                text(
                    "INSERT INTO artifacts (tenant_id, job_id, storage_key, content_type,"
                    " size_bytes) VALUES (:t, :j, 'k1', 'image/png', 1)"
                ),
                {"t": seed.tenant_a, "j": b["job"]},
            )


# --- rules of the model ------------------------------------------------------------------------


async def test_one_active_job_per_machine_is_enforced_by_the_database(
    app_factory: Factory, seed: Seed
) -> None:
    w = await _world(app_factory, seed.tenant_a)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        await _job(
            s, seed.tenant_a, w["bot"], w["pool"], status="assigned", machine_id=w["machine"]
        )
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="uq_jobs_one_active_per_machine"):
            await _job(
                s, seed.tenant_a, w["bot"], w["pool"], status="running", machine_id=w["machine"]
            )
    # A finished job does not count, and neither do pending ones.
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        await _job(
            s,
            seed.tenant_a,
            w["bot"],
            w["pool"],
            status="completed",
            machine_id=w["machine"],
            finished_at=datetime.now(UTC),
        )
        await _job(s, seed.tenant_a, w["bot"], w["pool"])
        await _job(s, seed.tenant_a, w["bot"], w["pool"])


async def test_job_checks(app_factory: Factory, seed: Seed) -> None:
    w = await _world(app_factory, seed.tenant_a)
    bad: list[dict[str, object]] = [
        {"status": "weird"},
        {"trigger": "carrier-pigeon"},
        {"error_code": "kaboom"},
        {"short_code": "exec-ZZZZZZ"},
        {"status": "running"},  # running needs a machine
        {"status": "completed"},  # finished needs finished_at
        {"finished_at": datetime.now(UTC)},  # finished_at needs a final status
        {"params": '{"x": "' + "a" * 9000 + '"}'},
    ]
    for fields in bad:
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            with pytest.raises(DBAPIError):
                if "params" in fields:
                    await s.execute(
                        text(
                            "INSERT INTO jobs (tenant_id, bot_id, pool_id, short_code, params)"
                            " VALUES (:t, :b, :p, :c, CAST(:x AS jsonb))"
                        ),
                        {
                            "t": seed.tenant_a,
                            "b": w["bot"],
                            "p": w["pool"],
                            "c": _code(),
                            "x": fields["params"],
                        },
                    )
                else:
                    await _job(s, seed.tenant_a, w["bot"], w["pool"], **fields)


async def test_short_code_is_unique_per_tenant_and_bot_names_too(
    app_factory: Factory, seed: Seed
) -> None:
    w = await _world(app_factory, seed.tenant_a)
    code = _code()
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        await _job(s, seed.tenant_a, w["bot"], w["pool"], short_code=code)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="uq_jobs_tenant_short_code"):
            await _job(s, seed.tenant_a, w["bot"], w["pool"], short_code=code)
    # Another client may reuse the code.
    other = await _world(app_factory, seed.tenant_b)
    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as s:
        await _job(s, seed.tenant_b, other["bot"], other["pool"], short_code=code)

    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        await s.execute(
            text(
                "INSERT INTO bots (tenant_id, pool_id, name, package_name)"
                " VALUES (:t, :p, 'Busca Unica', 'busca_unica')"
            ),
            {"t": seed.tenant_a, "p": w["pool"]},
        )
    for name, pkg, match in (
        ("busca unica", "outro_pkg", "uq_bots_tenant_name"),
        ("Outro Nome", "busca_unica", "uq_bots_tenant_package"),
        ("Bom nome", "Bad-Package", "package_name_format"),
    ):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            with pytest.raises(DBAPIError, match=match):
                await s.execute(
                    text(
                        "INSERT INTO bots (tenant_id, pool_id, name, package_name)"
                        " VALUES (:t, :p, :n, :k)"
                    ),
                    {"t": seed.tenant_a, "p": w["pool"], "n": name, "k": pkg},
                )


async def test_only_inserts_and_updates_are_granted_where_the_model_says(
    app_factory: Factory, seed: Seed
) -> None:
    w = await _world(app_factory, seed.tenant_a)
    for statement in (
        "DELETE FROM jobs WHERE id = :j",
        "DELETE FROM bots WHERE id = :b",
        "DELETE FROM artifacts WHERE id = :a",
        "UPDATE job_logs SET message = 'x' WHERE job_id = :j",
        "DELETE FROM job_logs WHERE job_id = :j",
    ):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            with pytest.raises(DBAPIError, match="permission denied"):
                await s.execute(text(statement), {"j": w["job"], "b": w["bot"], "a": w["artifact"]})


# --- job_logs ----------------------------------------------------------------------------------


async def test_resending_a_log_line_does_not_duplicate_it(app_factory: Factory, seed: Seed) -> None:
    w = await _world(app_factory, seed.tenant_a)
    ts = datetime.now(UTC)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        for _ in range(2):
            await s.execute(
                text(
                    "INSERT INTO job_logs (tenant_id, job_id, seq, ts, level, message)"
                    " VALUES (:t, :j, 77, :ts, 'INFO', 'once')"
                    " ON CONFLICT (job_id, seq, ts) DO NOTHING"
                ),
                {"t": seed.tenant_a, "j": w["job"], "ts": ts},
            )
        count = (
            await s.execute(
                text("SELECT count(*) FROM job_logs WHERE job_id = :j AND seq = 77"),
                {"j": w["job"]},
            )
        ).scalar_one()
    assert count == 1


async def test_a_log_line_without_a_partition_fails_loudly(
    app_factory: Factory, seed: Seed
) -> None:
    w = await _world(app_factory, seed.tenant_a)
    far = datetime.now(UTC) + timedelta(days=366 * 5)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="no partition of relation"):
            await _log(s, seed.tenant_a, w["job"], 1, far)


async def test_partition_function_is_idempotent_and_creates_isolated_partitions(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    async with tenant_session(app_factory) as s:
        await s.execute(text("SELECT app.ensure_job_log_partitions(4)"))
        again = (await s.execute(text("SELECT app.ensure_job_log_partitions(4)"))).scalar_one()
        status = (await s.execute(text("SELECT * FROM app.job_logs_partition_status()"))).one()
    assert again == 0
    assert status.current_month and status.next_month

    async with tenant_session(owner_factory) as s:
        parts = (
            await s.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
                    " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,"
                    " has_table_privilege('regista_app', c.oid, 'SELECT') AS app_reads"
                    " FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
                    " WHERE i.inhparent = 'public.job_logs'::regclass"
                )
            )
        ).all()
    assert len(parts) >= 5
    for p in parts:
        assert p.relname.startswith("job_logs_"), p.relname
        assert p.relrowsecurity and p.relforcerowsecurity, p.relname
        assert p.policies == 4, p.relname
        assert not p.app_reads, f"{p.relname}: the app role must reach logs through the parent"


async def test_partition_function_takes_no_free_text_and_is_not_public(
    app_factory: Factory, owner_factory: Factory
) -> None:
    for bad in (-1, 25):
        async with tenant_session(app_factory) as s:
            with pytest.raises(DBAPIError, match="between 0 and 24"):
                await s.execute(text("SELECT app.ensure_job_log_partitions(:n)"), {"n": bad})
    async with tenant_session(owner_factory) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT p.oid::regprocedure::text AS sig, p.prosecdef,"
                    " EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl,"
                    "   acldefault('f', p.proowner))) a WHERE a.grantee = 0) AS public_exec,"
                    " has_function_privilege('regista_app', p.oid, 'EXECUTE') AS app_exec"
                    " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'app' AND p.proname IN"
                    " ('ensure_job_log_partitions', 'job_logs_partition_status')"
                )
            )
        ).all()
    assert len(rows) == 2
    for r in rows:
        assert r.prosecdef and not r.public_exec and r.app_exec, r.sig


# --- NOTIFY ------------------------------------------------------------------------------------


async def test_creating_a_job_notifies_with_tenant_and_pool(
    app_factory: Factory, seed: Seed, db_urls: DbUrls
) -> None:
    w = await _world(app_factory, seed.tenant_a)
    dsn = db_urls.app.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    received: asyncio.Queue[str] = asyncio.Queue()
    try:
        await conn.add_listener("regista_jobs", lambda *a: received.put_nowait(a[3]))
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            await _job(s, seed.tenant_a, w["bot"], w["pool"])
        payload = await asyncio.wait_for(received.get(), timeout=5)
    finally:
        await conn.close()
    assert payload == f"{seed.tenant_a}:{w['pool']}"
