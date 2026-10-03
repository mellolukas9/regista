"""Client context (rg_client), the scoped/writing helpers and role permissions."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.auth.context import ALL_CLIENTS, ClientScope
from regista_api.auth.deps import AppState, Auth, CurrentUser, SessionInfo
from regista_api.auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    permissions_for,
)
from regista_api.core.db import tenant_session

from .conftest import Seed
from .helpers import Env, csrf, invite_user, new_client, onboard

Factory = async_sessionmaker[AsyncSession]


async def _platform_admin(env: Env, internal: uuid.UUID) -> None:
    await onboard(
        env.app, env.client, internal, env.clock, role="tenant_admin", platform_admin=True
    )


@asynccontextmanager
async def _deactivated(env: Env, tenant_id: uuid.UUID) -> AsyncIterator[None]:
    factory = env.app.state.session_factory
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        await db.execute(
            text("UPDATE tenants SET is_active = false WHERE id = :t"), {"t": tenant_id}
        )
    try:
        yield
    finally:
        async with tenant_session(factory, tenant_id=tenant_id) as db:
            await db.execute(
                text("UPDATE tenants SET is_active = true WHERE id = :t"), {"t": tenant_id}
            )


# --- permissions ------------------------------------------------------------------------------


def test_permissions_by_role() -> None:
    assert ROLE_PERMISSIONS["tenant_admin"] == {Permission.USERS_MANAGE}
    assert ROLE_PERMISSIONS["operator"] == frozenset()
    assert ROLE_PERMISSIONS["viewer"] == frozenset()
    assert permissions_for(role="viewer", is_platform_admin=True) == frozenset(Permission)
    assert not has_permission(
        role="tenant_admin", is_platform_admin=False, permission=Permission.CLIENTS_CREATE
    )
    assert not has_permission(
        role="unknown", is_platform_admin=False, permission=Permission.USERS_MANAGE
    )


# --- selecting a client -----------------------------------------------------------------------


async def test_platform_admin_starts_in_all_clients(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    await _platform_admin(env, internal_tenant)
    me = (await env.client.get("/auth/me")).json()
    assert me["is_platform_admin"] is True
    assert me["tenant_id"] == str(internal_tenant)  # the hidden internal tenant
    assert me["context"] == {"all_clients": True, "client_id": None, "client_name": None}


async def test_platform_admin_picks_and_clears_a_client(
    env: Env, seed: Seed, internal_tenant: uuid.UUID, owner_factory: Factory
) -> None:
    await _platform_admin(env, internal_tenant)
    picked = await env.client.put(
        "/auth/context", json={"client_id": str(seed.tenant_a)}, headers=csrf(env.client)
    )
    assert picked.status_code == 200
    assert picked.json() == {
        "all_clients": False,
        "client_id": str(seed.tenant_a),
        "client_name": "Tenant A",
    }
    cookie = next(c for c in picked.headers.get_list("set-cookie") if c.startswith("rg_client="))
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert (await env.client.get("/auth/me")).json()["context"]["client_id"] == str(seed.tenant_a)

    cleared = await env.client.put(
        "/auth/context", json={"client_id": None}, headers=csrf(env.client)
    )
    assert cleared.json() == {"all_clients": True, "client_id": None, "client_name": None}
    assert "rg_client" not in env.client.cookies
    assert (await env.client.get("/auth/me")).json()["context"]["all_clients"] is True

    async with tenant_session(owner_factory, tenant_id=internal_tenant) as db:
        actions = [
            r[0]
            for r in await db.execute(
                text("SELECT action FROM audit_log WHERE action = 'auth.context_changed'")
            )
        ]
    assert len(actions) >= 2


async def test_invalid_client_choices_are_refused(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    await _platform_admin(env, internal_tenant)
    for client_id in (uuid.uuid4(), internal_tenant):  # unknown; the hidden internal tenant
        r = await env.client.put(
            "/auth/context", json={"client_id": str(client_id)}, headers=csrf(env.client)
        )
        assert r.status_code == 404, client_id
        assert r.json()["detail"] == {"code": "client_not_found"}

    async with _deactivated(env, seed.tenant_b):
        r = await env.client.put(
            "/auth/context", json={"client_id": str(seed.tenant_b)}, headers=csrf(env.client)
        )
        assert r.status_code == 404
    bad = await env.client.put(
        "/auth/context", json={"client_id": "nao-e-uuid"}, headers=csrf(env.client)
    )
    assert bad.status_code == 422


async def test_unusable_client_cookies_fall_back_to_all_clients(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    await _platform_admin(env, internal_tenant)
    for value in ("nao-e-uuid", str(uuid.uuid4()), str(internal_tenant), "x" * 200):
        env.client.cookies.set("rg_client", value)
        ctx = (await env.client.get("/auth/me")).json()["context"]
        assert ctx["all_clients"] is True, value

    env.client.cookies.set("rg_client", str(seed.tenant_b))
    async with _deactivated(env, seed.tenant_b):
        assert (await env.client.get("/auth/me")).json()["context"]["all_clients"] is True
    assert (await env.client.get("/auth/me")).json()["context"]["client_id"] == str(seed.tenant_b)


async def test_client_users_stay_in_their_own_client(env: Env, seed: Seed) -> None:
    await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    env.client.cookies.set("rg_client", str(seed.tenant_b))  # forged
    ctx = (await env.client.get("/auth/me")).json()["context"]
    assert ctx == {"all_clients": False, "client_id": str(seed.tenant_a), "client_name": "Tenant A"}

    forbidden = await env.client.put(
        "/auth/context", json={"client_id": str(seed.tenant_b)}, headers=csrf(env.client)
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == {"code": "forbidden"}


async def test_context_route_needs_a_session_and_csrf(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    body = {"client_id": str(seed.tenant_a)}
    assert (await env.client.put("/auth/context", json=body)).status_code == 401
    await _platform_admin(env, internal_tenant)
    r = await env.client.put("/auth/context", json=body)
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "csrf_invalid"}


async def test_deactivated_client_locks_its_users_out(env: Env, seed: Seed) -> None:
    await onboard(env.app, env.client, seed.tenant_b, env.clock, role="viewer")
    assert (await env.client.get("/auth/me")).status_code == 200
    async with _deactivated(env, seed.tenant_b):
        assert (await env.client.get("/auth/me")).status_code == 401
    assert (await env.client.get("/auth/me")).status_code == 200


# --- scoped() and writing() -------------------------------------------------------------------


def _auth(env: Env, tenant_id: uuid.UUID, scope: ClientScope) -> Auth:
    app = env.app
    return Auth(
        user=CurrentUser(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            tenant_name="x",
            email="x@example.com",
            role="tenant_admin",
            is_platform_admin=scope.all_clients,
            display_name=None,
            status="active",
            password_hash=None,
            mfa_enabled=True,
            mfa_enabled_at=None,
            mfa_secret_enc=None,
            mfa_key_id=None,
            mfa_last_step=None,
            failed_logins=0,
            locked_until=None,
        ),
        session=SessionInfo(id=uuid.uuid4(), stage="active", last_seen_at=None),  # type: ignore[arg-type]
        scope=scope,
        state=AppState(
            settings=app.state.settings,
            factory=app.state.session_factory,
            keys=app.state.key_provider,
            email=app.state.email_sender,
        ),
        ip=None,
        user_agent=None,
    )


async def test_scoped_reads_follow_the_client_context(env: Env, seed: Seed) -> None:
    await invite_user(env.app, seed.tenant_a)
    await invite_user(env.app, seed.tenant_b)

    in_a = _auth(env, seed.tenant_a, ClientScope(seed.tenant_a, "A"))
    async with in_a.scoped() as db:
        tenants = {r[0] for r in await db.execute(text("SELECT DISTINCT tenant_id FROM users"))}
    assert tenants == {seed.tenant_a}

    everyone = _auth(env, seed.tenant_a, ALL_CLIENTS)
    async with everyone.scoped() as db:
        tenants = {r[0] for r in await db.execute(text("SELECT DISTINCT tenant_id FROM users"))}
    assert {seed.tenant_a, seed.tenant_b} <= tenants


async def test_all_clients_is_read_only(env: Env, seed: Seed) -> None:
    await invite_user(env.app, seed.tenant_a)
    everyone = _auth(env, seed.tenant_a, ALL_CLIENTS)

    with pytest.raises(HTTPException) as refused:
        async with everyone.writing():
            pass
    assert refused.value.status_code == 409
    detail: object = refused.value.detail
    assert detail == {"code": "client_context_required"}

    # Even through scoped(), RLS lets the read flag see rows but not change them.
    async with everyone.scoped() as db:
        changed = await db.execute(text("UPDATE users SET display_name = 'x'"))
        deleted = await db.execute(text("DELETE FROM recovery_codes"))
    assert changed.rowcount == 0  # type: ignore[attr-defined]
    assert deleted.rowcount == 0  # type: ignore[attr-defined]


async def test_writing_in_a_client_cannot_touch_another(env: Env, seed: Seed) -> None:
    user_b, _, _ = await invite_user(env.app, seed.tenant_b)
    in_a = _auth(env, seed.tenant_a, ClientScope(seed.tenant_a, "A"))
    async with in_a.writing() as db:
        result = await db.execute(
            text("UPDATE users SET display_name = 'hack' WHERE id = :u"), {"u": user_b}
        )
    assert result.rowcount == 0  # type: ignore[attr-defined]
    other = new_client(env.app)
    await other.aclose()
