"""Proves tenant isolation at the database level (ADR 0004, docs/specs/security.md)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import Seed

Factory = async_sessionmaker[AsyncSession]


async def _labels(session: AsyncSession) -> list[str]:
    # Deliberately no WHERE clause: isolation must come from RLS alone.
    rows = await session.execute(text("SELECT label FROM rls_probe ORDER BY label"))
    return [r[0] for r in rows]


async def _slugs(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        text("SELECT slug FROM tenants WHERE slug IN ('a', 'b') ORDER BY slug")
    )
    return [r[0] for r in rows]


async def test_app_role_is_not_privileged(app_factory: Factory, seed: Seed) -> None:
    async with tenant_session(app_factory) as session:
        role = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        owned: int = (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_tables"
                    " WHERE schemaname IN ('public', 'app') AND tableowner = current_user"
                )
            )
        ).scalar_one()
    assert role.rolsuper is False
    assert role.rolbypassrls is False
    assert owned == 0


async def test_every_tenant_table_has_forced_rls_and_policies(owner_factory: Factory) -> None:
    """Guard for future milestones: new tables cannot ship without the pattern."""
    async with tenant_session(owner_factory) as session:
        tables = (
            await session.execute(
                text(
                    "SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity,"
                    " EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
                    "         AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant_id"
                    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
                    " AND c.relname <> 'alembic_version'"
                )
            )
        ).all()
        assert {t.relname for t in tables} >= {"tenants", "rls_probe"}
        for table in tables:
            assert table.relrowsecurity, f"{table.relname}: RLS not enabled"
            assert table.relforcerowsecurity, f"{table.relname}: RLS not forced"
            commands = {
                r[0]
                for r in await session.execute(
                    text("SELECT polcmd::text FROM pg_policy WHERE polrelid = :oid"),
                    {"oid": table.oid},
                )
            }
            expected = {"r", "a", "w", "d"} if table.has_tenant_id else {"r", "a", "w"}
            assert expected <= commands, f"{table.relname}: missing policies {expected - commands}"


async def test_no_tenant_returns_zero_rows(app_factory: Factory, seed: Seed) -> None:
    async with tenant_session(app_factory) as session:
        assert await _labels(session) == []
        assert await _slugs(session) == []


async def test_query_without_where_returns_only_session_tenant(
    app_factory: Factory, seed: Seed
) -> None:
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        assert await _labels(session) == ["a1", "a2"]
        assert await _slugs(session) == ["a"]
    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        assert await _labels(session) == ["b1", "b2"]
        assert await _slugs(session) == ["b"]


async def test_force_rls_applies_to_the_table_owner(owner_factory: Factory, seed: Seed) -> None:
    async with tenant_session(owner_factory) as session:
        assert await _labels(session) == []
        assert await _slugs(session) == []


async def test_cross_tenant_writes_are_rejected(app_factory: Factory, seed: Seed) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await session.execute(
                text("INSERT INTO rls_probe (tenant_id, label) VALUES (:t, 'evil')"),
                {"t": seed.tenant_b},
            )

    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        updated = await session.execute(text("UPDATE rls_probe SET label = 'x' WHERE label = 'b1'"))
        deleted = await session.execute(text("DELETE FROM rls_probe WHERE label LIKE 'b%'"))
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        assert deleted.rowcount == 0  # type: ignore[attr-defined]

    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        assert await _labels(session) == ["b1", "b2"]


async def test_moving_a_row_to_another_tenant_is_rejected(app_factory: Factory, seed: Seed) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await session.execute(
                text("UPDATE rls_probe SET tenant_id = :b WHERE label = 'a1'"),
                {"b": seed.tenant_b},
            )


async def test_tenant_user_cannot_create_tenants(app_factory: Factory, seed: Seed) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await session.execute(
                text("INSERT INTO tenants (name, slug, data_region) VALUES ('x', 'x', 'sa-east-1')")
            )


async def test_platform_admin_reads_everything_but_cannot_write_across_tenants(
    app_factory: Factory, seed: Seed
) -> None:
    async with tenant_session(app_factory, platform_admin=True) as session:
        assert await _labels(session) == ["a1", "a2", "b1", "b2"]
        assert await _slugs(session) == ["a", "b"]

        updated = await session.execute(text("UPDATE rls_probe SET label = label"))
        deleted = await session.execute(text("DELETE FROM rls_probe"))
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        assert deleted.rowcount == 0  # type: ignore[attr-defined]

    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_factory, platform_admin=True) as session:
            await session.execute(
                text("INSERT INTO rls_probe (tenant_id, label) VALUES (:t, 'evil')"),
                {"t": seed.tenant_a},
            )

    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        assert await _labels(session) == ["a1", "a2"]


async def test_tenant_context_does_not_leak_through_the_pool(
    app_factory: Factory, seed: Seed
) -> None:
    async with tenant_session(app_factory, tenant_id=seed.tenant_a, platform_admin=True) as session:
        pid_with_tenant: int = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert await _labels(session) == ["a1", "a2", "b1", "b2"]

    # Pool size is 1, so this reuses the same connection, with no tenant set.
    async with tenant_session(app_factory) as session:
        pid_after: int = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert pid_after == pid_with_tenant
        assert await _labels(session) == []
        assert await _slugs(session) == []
