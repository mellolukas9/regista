"""Database-level guarantees of the identity tables and the lookup functions (ADR 0017)."""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import Seed

Factory = async_sessionmaker[AsyncSession]


COUNT_SQL = {
    t: f"SELECT count(*) FROM {t}"  # noqa: S608  (fixed table names)
    for t in ("users", "invitations", "sessions", "recovery_codes")
}


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@Example.com"


async def _insert_user(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    email: str | None = None,
    platform_admin: bool = False,
) -> uuid.UUID:
    result = await session.execute(
        text(
            "INSERT INTO users (tenant_id, email, role, is_platform_admin)"
            " VALUES (:t, :e, :r, :p) RETURNING id"
        ),
        {
            "t": tenant_id,
            "e": email or _email(),
            "r": "tenant_admin" if platform_admin else "viewer",
            "p": platform_admin,
        },
    )
    return result.scalar_one()


async def _user_with_invitation_and_session(
    factory: Factory, tenant_id: uuid.UUID
) -> tuple[uuid.UUID, str, bytes, bytes]:
    invite_hash, session_hash = os.urandom(32), os.urandom(32)
    email = _email()
    async with tenant_session(factory, tenant_id=tenant_id) as session:
        user_id = await _insert_user(session, tenant_id, email=email)
        await session.execute(
            text(
                "INSERT INTO invitations (tenant_id, user_id, token_hash, expires_at)"
                " VALUES (:t, :u, :h, now() + interval '7 days')"
            ),
            {"t": tenant_id, "u": user_id, "h": invite_hash},
        )
        await session.execute(
            text(
                "INSERT INTO sessions (tenant_id, user_id, token_hash, csrf_token_hash, stage,"
                " expires_at) VALUES (:t, :u, :h, :c, 'active', now() + interval '1 day')"
            ),
            {"t": tenant_id, "u": user_id, "h": session_hash, "c": os.urandom(32)},
        )
    return user_id, email, invite_hash, session_hash


# --- visibility -------------------------------------------------------------------------------


async def test_identity_rows_are_invisible_without_tenant_and_across_tenants(
    app_factory: Factory, seed: Seed
) -> None:
    await _user_with_invitation_and_session(app_factory, seed.tenant_a)
    tables = ("users", "invitations", "sessions", "recovery_codes")

    async with tenant_session(app_factory) as session:
        for table in tables:
            none_visible: int = (await session.execute(text(COUNT_SQL[table]))).scalar_one()
            assert none_visible == 0, table

    async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
        for table in tables:
            other_tenant_rows: int = (await session.execute(text(COUNT_SQL[table]))).scalar_one()
            assert other_tenant_rows == 0, table

    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        assert (await session.execute(text("SELECT count(*) FROM users"))).scalar_one() >= 1


async def test_cannot_create_a_user_in_another_tenant(app_factory: Factory, seed: Seed) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await _insert_user(session, seed.tenant_b)


async def test_email_is_unique_across_tenants_and_case_insensitive(
    app_factory: Factory, seed: Seed
) -> None:
    email = _email()
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        await _insert_user(session, seed.tenant_a, email=email)
    with pytest.raises(DBAPIError, match="uq_users_email"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_b) as session:
            await _insert_user(session, seed.tenant_b, email=email.upper())


# --- audit_log is append-only ----------------------------------------------------------------


async def test_audit_log_is_insert_only_for_the_app_role(app_factory: Factory, seed: Seed) -> None:
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
        await session.execute(
            text(
                "INSERT INTO audit_log (tenant_id, actor_type, action)"
                " VALUES (:t, 'system', 'test.event')"
            ),
            {"t": seed.tenant_a},
        )
    for statement in (
        "SELECT * FROM audit_log",
        "UPDATE audit_log SET action = 'x'",
        "DELETE FROM audit_log",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
                await session.execute(text(statement))


# --- lookup functions -------------------------------------------------------------------------


async def test_lookup_login_returns_only_what_authentication_needs(
    app_factory: Factory, seed: Seed
) -> None:
    user_id, email, _, _ = await _user_with_invitation_and_session(app_factory, seed.tenant_a)

    # No tenant in the transaction: this is the pre-login path.
    async with tenant_session(app_factory) as session:
        result = await session.execute(
            text("SELECT * FROM app.lookup_login(:e)"), {"e": email.lower()}
        )
        rows = result.mappings().all()
        assert len(rows) == 1
        assert set(rows[0]) == {
            "id",
            "tenant_id",
            "password_hash",
            "status",
            "mfa_enabled",
            "failed_logins",
            "locked_until",
            "is_platform_admin",
            "tenant_active",
        }
        assert rows[0]["id"] == user_id
        assert rows[0]["tenant_id"] == seed.tenant_a
        assert rows[0]["tenant_active"] is True

        missing = await session.execute(
            text("SELECT * FROM app.lookup_login('nobody@example.com')")
        )
        assert missing.all() == []


async def test_lookup_invitation_and_session_match_only_the_exact_hash(
    app_factory: Factory, seed: Seed
) -> None:
    user_id, email, invite_hash, session_hash = await _user_with_invitation_and_session(
        app_factory, seed.tenant_b
    )
    async with tenant_session(app_factory) as session:
        invitation = (
            (
                await session.execute(
                    text("SELECT * FROM app.lookup_invitation(:h)"), {"h": invite_hash}
                )
            )
            .mappings()
            .one()
        )
        assert set(invitation) == {
            "invitation_id",
            "tenant_id",
            "user_id",
            "email",
            "expires_at",
            "used_at",
            "revoked_at",
        }
        assert invitation["user_id"] == user_id
        assert invitation["email"].lower() == email.lower()
        assert invitation["tenant_id"] == seed.tenant_b

        found = (
            (
                await session.execute(
                    text("SELECT * FROM app.lookup_session(:h)"), {"h": session_hash}
                )
            )
            .mappings()
            .one()
        )
        assert set(found) == {
            "session_id",
            "tenant_id",
            "user_id",
            "stage",
            "csrf_token_hash",
            "expires_at",
            "last_seen_at",
            "revoked_at",
        }
        assert found["stage"] == "active"

        for fn in ("lookup_invitation", "lookup_session"):
            miss = await session.execute(
                text(f"SELECT * FROM app.{fn}(:h)"),  # noqa: S608  (fixed function names)
                {"h": os.urandom(32)},
            )
            assert miss.all() == []


async def test_definer_functions_are_locked_down(app_factory: Factory, seed: Seed) -> None:
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
                    {
                        "names": [
                            "lookup_login",
                            "lookup_invitation",
                            "lookup_session",
                            "rate_limit_hit",
                            "users_guard_platform_admin",
                        ]
                    },
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 5
    for row in rows:
        assert row["prosecdef"] is True, row["proname"]
        assert row["owner"] == "regista_owner", row["proname"]
        assert "search_path=pg_catalog, public" in row["config"], row["proname"]
        # No grant to PUBLIC (an aclitem with an empty grantee looks like `=X/owner`).
        assert "{=" not in row["acl"] and ",=" not in row["acl"], row["proname"]
        if row["proname"] != "users_guard_platform_admin":
            assert "regista_app=X/regista_owner" in row["acl"], row["proname"]


# --- rate limit -------------------------------------------------------------------------------


async def test_rate_limit_counts_in_the_database_and_table_is_not_directly_reachable(
    app_factory: Factory, seed: Seed
) -> None:
    key = os.urandom(32)
    results: list[bool] = []
    for _ in range(4):
        async with tenant_session(app_factory) as session:
            hit = await session.execute(text("SELECT app.rate_limit_hit(:k, 60, 3)"), {"k": key})
            results.append(hit.scalar_one())
    assert results == [False, False, False, True]

    async with tenant_session(app_factory) as session:
        other = await session.execute(
            text("SELECT app.rate_limit_hit(:k, 60, 3)"), {"k": os.urandom(32)}
        )
        assert other.scalar_one() is False

    for statement in ("SELECT * FROM auth_rate_limits", "DELETE FROM auth_rate_limits"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_session(app_factory, platform_admin=True) as session:
                await session.execute(text(statement))


# --- internal tenant invariants ---------------------------------------------------------------


async def test_only_one_internal_tenant_and_it_cannot_change_or_be_deactivated(
    app_factory: Factory, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError, match="uq_tenants_single_internal"):
        async with tenant_session(app_factory, platform_admin=True) as session:
            await session.execute(
                text(
                    "INSERT INTO tenants (name, slug, data_region, is_internal)"
                    " VALUES ('Second', 'second-internal', 'sa-east-1', true)"
                )
            )
    with pytest.raises(DBAPIError, match="ck_tenants_internal_active"):
        async with tenant_session(app_factory, tenant_id=internal_tenant) as session:
            await session.execute(
                text("UPDATE tenants SET is_active = false WHERE id = :t"), {"t": internal_tenant}
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with tenant_session(app_factory, tenant_id=internal_tenant) as session:
            await session.execute(
                text("UPDATE tenants SET is_internal = false WHERE id = :t"),
                {"t": internal_tenant},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await session.execute(
                text("UPDATE tenants SET is_internal = true WHERE id = :t"), {"t": seed.tenant_a}
            )


async def test_platform_admin_flag_only_exists_in_the_internal_tenant(
    app_factory: Factory, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError, match=r"must match tenants\.is_internal"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as session:
            await _insert_user(session, seed.tenant_a, platform_admin=True)
    with pytest.raises(DBAPIError, match=r"must match tenants\.is_internal"):
        async with tenant_session(app_factory, tenant_id=internal_tenant) as session:
            await _insert_user(session, internal_tenant, platform_admin=False)

    async with tenant_session(app_factory, tenant_id=internal_tenant) as session:
        user_id = await _insert_user(session, internal_tenant, platform_admin=True)
    with pytest.raises(DBAPIError, match=r"must match tenants\.is_internal"):
        async with tenant_session(app_factory, tenant_id=internal_tenant) as session:
            await session.execute(
                text("UPDATE users SET is_platform_admin = false WHERE id = :u"), {"u": user_id}
            )
