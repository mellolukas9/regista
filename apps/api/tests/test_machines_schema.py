"""Database-level guarantees of pools, machines, enrollment keys and events (M2, ADR 0018)."""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import Seed

Factory = async_sessionmaker[AsyncSession]

TABLES = ("pools", "machines", "enrollment_keys", "machine_events")
FOREIGN_SQL = {
    t: f"SELECT count(*) FROM {t} WHERE tenant_id <> :t"  # noqa: S608  (fixed table names)
    for t in TABLES
}
COUNT_SQL = {t: f"SELECT count(*) FROM {t}" for t in TABLES}  # noqa: S608  (fixed table names)


def _name() -> str:
    return f"m-{uuid.uuid4().hex[:10]}"


async def _pool(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    result = await session.execute(
        text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n) RETURNING id"),
        {"t": tenant_id, "n": _name()},
    )
    return result.scalar_one()


async def _machine(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pool_id: uuid.UUID,
    *,
    name: str | None = None,
) -> uuid.UUID:
    result = await session.execute(
        text("INSERT INTO machines (tenant_id, pool_id, name) VALUES (:t, :p, :n) RETURNING id"),
        {"t": tenant_id, "p": pool_id, "n": _name() if name is None else name},
    )
    return result.scalar_one()


async def _key(
    session: AsyncSession, tenant_id: uuid.UUID, machine_id: uuid.UUID, key_hash: bytes
) -> None:
    await session.execute(
        text(
            "INSERT INTO enrollment_keys (tenant_id, machine_id, key_hash, expires_at)"
            " VALUES (:t, :m, :h, now() + interval '24 hours')"
        ),
        {"t": tenant_id, "m": machine_id, "h": key_hash},
    )


async def _world(factory: Factory, tenant_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, bytes]:
    key_hash = os.urandom(32)
    async with tenant_session(factory, tenant_id=tenant_id) as session:
        pool_id = await _pool(session, tenant_id)
        machine_id = await _machine(session, tenant_id, pool_id)
        await _key(session, tenant_id, machine_id, key_hash)
        await session.execute(
            text(
                "INSERT INTO machine_events (tenant_id, machine_id, kind)"
                " VALUES (:t, :m, 'enrolled')"
            ),
            {"t": tenant_id, "m": machine_id},
        )
    return pool_id, machine_id, key_hash


# --- visibility -------------------------------------------------------------------------------


async def test_machine_rows_are_invisible_without_tenant_and_across_tenants(
    app_factory: Factory, seed: Seed
) -> None:
    await _world(app_factory, seed.tenant_a)

    async with tenant_session(app_factory) as session:
        for table in TABLES:
            assert (await session.execute(text(COUNT_SQL[table]))).scalar_one() == 0, table

    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        for table in TABLES:
            foreign: int = (
                await session.execute(text(FOREIGN_SQL[table]), {"t": seed.tenant_b})
            ).scalar_one()
            assert foreign == 0, table

    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        for table in TABLES:
            assert (await session.execute(text(COUNT_SQL[table]))).scalar_one() >= 1, table


async def test_cannot_write_machine_rows_for_another_tenant(
    app_factory: Factory, seed: Seed
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await _pool(session, seed.tenant_b)


async def test_composite_foreign_keys_tie_children_to_the_same_tenant(
    app_factory: Factory, seed: Seed
) -> None:
    pool_a, machine_a, _ = await _world(app_factory, seed.tenant_a)

    # B's own tenant_id passes RLS, but the pool and the machine belong to A.
    with pytest.raises(DBAPIError, match="fk_machines_tenant_pool_pools"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
            await _machine(session, seed.tenant_b, pool_a)
    # A used key stays out of the "one live key" index, so only the foreign key can refuse it.
    with pytest.raises(DBAPIError, match="fk_enrollment_keys_tenant_machine_machines"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
            await session.execute(
                text(
                    "INSERT INTO enrollment_keys (tenant_id, machine_id, key_hash, expires_at,"
                    " used_at) VALUES (:t, :m, :h, now() + interval '1 hour', now())"
                ),
                {"t": seed.tenant_b, "m": machine_a, "h": os.urandom(32)},
            )
    with pytest.raises(DBAPIError, match="fk_machine_events_tenant_machine_machines"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
            await session.execute(
                text(
                    "INSERT INTO machine_events (tenant_id, machine_id, kind)"
                    " VALUES (:t, :m, 'enrolled')"
                ),
                {"t": seed.tenant_b, "m": machine_a},
            )


# --- constraints ------------------------------------------------------------------------------


async def test_machine_constraints(app_factory: Factory, seed: Seed) -> None:
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        pool_id = await _pool(session, seed.tenant_a)
        name = _name()
        await _machine(session, seed.tenant_a, pool_id, name=name)

    # Name format: lowercase letters, digits and hyphen, starting with a letter or digit.
    for bad in ("Upper", "-leading", "has space", "a" * 64, ""):
        with pytest.raises(DBAPIError, match="name_format"):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
                await _machine(session, seed.tenant_a, pool_id, name=bad)

    # Unique per tenant among the non-revoked, free again after revocation.
    with pytest.raises(DBAPIError, match="uq_machines_tenant_name"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await _machine(session, seed.tenant_a, pool_id, name=name)
    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        await _machine(session, seed.tenant_b, await _pool(session, seed.tenant_b), name=name)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        await session.execute(
            text(
                "UPDATE machines SET status = 'revoked', revoked_at = now(),"
                " public_key = :k WHERE tenant_id = :t AND name = :n"
            ),
            {"k": os.urandom(32), "t": seed.tenant_a, "n": name},
        )
        await _machine(session, seed.tenant_a, pool_id, name=name)

    # status <=> revoked_at, and a machine past `pending` has a key of 32 bytes (unless it was
    # revoked before it ever enrolled, which is allowed). The database
    # is shared by the whole session, so each statement targets this test's own machine.
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        own = await _machine(session, seed.tenant_a, pool_id)
    for pattern, statement, params in (
        ("ck_machines_revoked", "UPDATE machines SET revoked_at = now() WHERE id = :m", {}),
        ("ck_machines_enrolled_key", "UPDATE machines SET status = 'online' WHERE id = :m", {}),
        (
            "ck_machines_public_key_size",
            "UPDATE machines SET public_key = :k WHERE id = :m",
            {"k": os.urandom(31)},
        ),
    ):
        with pytest.raises(DBAPIError, match=pattern):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
                await session.execute(text(statement), {"m": own, **params})


async def test_pool_names_are_unique_per_tenant_ignoring_case(
    app_factory: Factory, seed: Seed
) -> None:
    name = _name()
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        await session.execute(
            text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n)"),
            {"t": seed.tenant_a, "n": name},
        )
    with pytest.raises(DBAPIError, match="uq_pools_tenant_name"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await session.execute(
                text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n)"),
                {"t": seed.tenant_a, "n": name.upper()},
            )
    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        await session.execute(
            text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n)"),
            {"t": seed.tenant_b, "n": name},
        )


async def test_a_machine_has_only_one_live_enrollment_key(app_factory: Factory, seed: Seed) -> None:
    _, machine_id, _ = await _world(app_factory, seed.tenant_a)

    with pytest.raises(DBAPIError, match="uq_enrollment_keys_live"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await _key(session, seed.tenant_a, machine_id, os.urandom(32))

    # Revoking the live key (what "generate a new key" does first) frees the slot.
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        await session.execute(
            text(
                "UPDATE enrollment_keys SET revoked_at = now()"
                " WHERE machine_id = :m AND used_at IS NULL AND revoked_at IS NULL"
            ),
            {"m": machine_id},
        )
        await _key(session, seed.tenant_a, machine_id, os.urandom(32))


async def test_machine_events_are_insert_only_for_the_app_role(
    app_factory: Factory, seed: Seed
) -> None:
    await _world(app_factory, seed.tenant_a)
    for statement in (
        "UPDATE machine_events SET kind = 'revoked'",
        "DELETE FROM machine_events",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
                await session.execute(text(statement))
    with pytest.raises(DBAPIError, match="ck_machine_events_kind"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await session.execute(
                text(
                    "INSERT INTO machine_events (tenant_id, machine_id, kind)"
                    " SELECT tenant_id, id, 'bogus' FROM machines LIMIT 1"
                )
            )


# --- lookup functions -------------------------------------------------------------------------


async def test_lookup_enrollment_key_returns_only_what_enrollment_needs(
    app_factory: Factory, seed: Seed
) -> None:
    _, machine_id, key_hash = await _world(app_factory, seed.tenant_a)

    async with tenant_session(app_factory) as session:
        # No tenant in the transaction: the table is invisible, the function still finds the key.
        assert (await session.execute(text(COUNT_SQL["enrollment_keys"]))).scalar_one() == 0
        row = (
            (
                await session.execute(
                    text("SELECT * FROM app.lookup_enrollment_key(:h)"), {"h": key_hash}
                )
            )
            .mappings()
            .one()
        )
        assert set(row) == {
            "key_id",
            "tenant_id",
            "machine_id",
            "expires_at",
            "used_at",
            "revoked_at",
            "machine_status",
        }
        assert row["tenant_id"] == seed.tenant_a
        assert row["machine_id"] == machine_id
        assert row["machine_status"] == "pending"
        assert row["used_at"] is None and row["revoked_at"] is None

        miss = await session.execute(
            text("SELECT * FROM app.lookup_enrollment_key(:h)"), {"h": os.urandom(32)}
        )
        assert miss.all() == []


async def test_lookup_machine_credential_returns_only_what_the_challenge_needs(
    app_factory: Factory, seed: Seed
) -> None:
    _, machine_id, _ = await _world(app_factory, seed.tenant_b)
    public_key = os.urandom(32)
    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        await session.execute(
            text(
                "UPDATE machines SET public_key = :k, status = 'online',"
                " credential_version = 4 WHERE id = :m"
            ),
            {"k": public_key, "m": machine_id},
        )

    async with tenant_session(app_factory) as session:
        assert (await session.execute(text(COUNT_SQL["machines"]))).scalar_one() == 0
        row = (
            (
                await session.execute(
                    text("SELECT * FROM app.lookup_machine_credential(:m)"), {"m": machine_id}
                )
            )
            .mappings()
            .one()
        )
        assert set(row) == {"tenant_id", "public_key", "credential_version", "status"}
        assert row["tenant_id"] == seed.tenant_b
        assert bytes(row["public_key"]) == public_key
        assert row["credential_version"] == 4
        assert row["status"] == "online"

        miss = await session.execute(
            text("SELECT * FROM app.lookup_machine_credential(:m)"), {"m": uuid.uuid4()}
        )
        assert miss.all() == []


async def test_new_definer_functions_are_locked_down(app_factory: Factory, seed: Seed) -> None:
    names = ["lookup_enrollment_key", "lookup_machine_credential", "rate_limit_purge"]
    async with tenant_session(app_factory) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner,"
                        " coalesce(p.proconfig, '{}')::text AS config,"
                        " coalesce(p.proacl::text, '') AS acl"
                        " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                        " WHERE n.nspname = 'app' AND p.proname = ANY(:names)"
                    ),
                    {"names": names},
                )
            )
            .mappings()
            .all()
        )
    assert {r["proname"] for r in rows} == set(names)
    for row in rows:
        assert row["prosecdef"] is True, row["proname"]
        assert row["owner"] == "regista_owner", row["proname"]
        assert "search_path=pg_catalog, public" in row["config"], row["proname"]
        assert "{=" not in row["acl"] and ",=" not in row["acl"], row["proname"]
        assert "regista_app=X/regista_owner" in row["acl"], row["proname"]


async def test_rate_limit_purge_removes_only_old_windows(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    old_key, fresh_key = os.urandom(32), os.urandom(32)
    async with tenant_session(owner_factory, platform_admin=True) as session:
        await session.execute(text("SELECT set_config('app.rate_limit', 'on', true)"))
        await session.execute(
            text(
                "INSERT INTO auth_rate_limits (key_hash, window_start, count)"
                " VALUES (:o, now() - interval '2 days', 1), (:f, now(), 1)"
            ),
            {"o": old_key, "f": fresh_key},
        )

    async with tenant_session(app_factory) as session:
        removed: int = (
            await session.execute(text("SELECT app.rate_limit_purge(86400)"))
        ).scalar_one()
        assert removed >= 1
        flag: str = (
            await session.execute(
                text("SELECT coalesce(current_setting('app.rate_limit', true), '')")
            )
        ).scalar_one()
        assert flag != "on"

    async with tenant_session(owner_factory, platform_admin=True) as session:
        await session.execute(text("SELECT set_config('app.rate_limit', 'on', true)"))
        left = {
            bytes(r[0])
            for r in await session.execute(
                text("SELECT key_hash FROM auth_rate_limits WHERE key_hash IN (:o, :f)"),
                {"o": old_key, "f": fresh_key},
            )
        }
    assert left == {fresh_key}


# --- Procrastinate ----------------------------------------------------------------------------


async def test_app_role_can_use_the_job_queue_without_owning_it(
    app_factory: Factory, seed: Seed
) -> None:
    async with tenant_session(app_factory) as session:
        job_id: int = (
            await session.execute(
                text(
                    "INSERT INTO procrastinate_jobs (queue_name, task_name, args)"
                    " VALUES ('default', 'schema.smoke', '{}'::jsonb) RETURNING id"
                )
            )
        ).scalar_one()
        assert job_id >= 1
        # Status events are written by a trigger running with the app role's privileges.
        events: int = (
            await session.execute(
                text("SELECT count(*) FROM procrastinate_events WHERE job_id = :j"),
                {"j": job_id},
            )
        ).scalar_one()
        assert events >= 1
        await session.execute(text("DELETE FROM procrastinate_jobs WHERE id = :j"), {"j": job_id})
        # The queue functions are callable by the app role (register a worker, then remove it).
        worker_id: int = (
            await session.execute(text("SELECT procrastinate_register_worker_v1()"))
        ).scalar_one()
        await session.execute(
            text("SELECT procrastinate_unregister_worker_v1(:w)"), {"w": worker_id}
        )
