"""Cross-client isolation, proven for every route the app has.

Routes are discovered from the running app (tests/isolation.py). Each one is called by the most
privileged user of client B, and by the Artemisys team working in client B, against resources
that belong to client A. The expectation is 404 for anything that names a resource of A, no
server errors anywhere, no trace of A in any response, and A's data untouched afterwards.
"""

import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.auth.deps import PublicRoute, Require, RequireStage, SelfService
from regista_api.auth.machine import MachineRoute
from regista_api.auth.permissions import PLATFORM_ONLY
from regista_api.core.db import tenant_session
from regista_api.main import create_app

from .conftest import DbUrls, Seed, make_settings, open_env
from .helpers import Account, Env, csrf, invite_user, login, new_client, onboard, unique_email
from .isolation import RouteSpec, Uncovered, discover

Factory = async_sessionmaker[AsyncSession]

# Path parameters that name a resource owned by a client, and where to find an A example.
RESOURCE_PARAMS = {"user_id", "session_id"}


@dataclass
class World:
    env: Env
    seed: Seed
    internal: uuid.UUID
    b_admin: Account
    staff: Account
    a_ids: dict[str, str]
    a_markers: list[str]  # strings that must never show up in anything B or staff-in-B receives


@pytest_asyncio.fixture
async def world(db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID) -> AsyncIterator[World]:
    async with open_env(
        db_urls,
        rate_login_email_per_15min=1_000_000,
        rate_mfa_per_5min=1_000_000,
        lockout_threshold=1_000_000,
    ) as env:
        a_admin = await onboard(
            env.app,
            new_client(env.app),
            seed.tenant_a,
            env.clock,
            role="tenant_admin",
            email=unique_email("leak-admin"),
        )
        a_target = await onboard(
            env.app, new_client(env.app), seed.tenant_a, env.clock, email=unique_email("leak-user")
        )
        b_admin = await onboard(
            env.app, new_client(env.app), seed.tenant_b, env.clock, role="tenant_admin"
        )
        staff = await onboard(
            env.app,
            new_client(env.app),
            internal_tenant,
            env.clock,
            role="tenant_admin",
            platform_admin=True,
        )

        async with tenant_session(env.app.state.session_factory, tenant_id=seed.tenant_a) as db:
            target_id: uuid.UUID = (
                await db.execute(
                    text("SELECT id FROM users WHERE email = :e"), {"e": a_target.email}
                )
            ).scalar_one()
            session_id: uuid.UUID = (
                await db.execute(
                    text("SELECT id FROM sessions WHERE user_id = :u LIMIT 1"),
                    {"u": a_admin.user_id},
                )
            ).scalar_one()
        yield World(
            env=env,
            seed=seed,
            internal=internal_tenant,
            b_admin=b_admin,
            staff=staff,
            a_ids={"user_id": str(target_id), "session_id": str(session_id)},
            a_markers=[
                str(seed.tenant_a),
                "Tenant A",
                a_admin.email,
                a_target.email,
                str(target_id),
                str(session_id),
            ],
        )


# --- helpers ----------------------------------------------------------------------------------


async def _b_client(w: World, spec: RouteSpec) -> httpx.AsyncClient:
    """A fresh browser of client B in the stage the route needs (a new one per route)."""
    env, app, b = w.env, w.env.app, w.seed.tenant_b
    browser = new_client(app)
    stages: tuple[str, ...] = getattr(spec.marker, "stages", ())
    stage = stages[0] if len(stages) == 1 else "active"
    if spec.marker.kind in ("public",):
        return browser
    if stage == "active":
        await login(browser, w.b_admin, env.clock)
    elif stage == "mfa_required":
        r = await browser.post(
            "/auth/login", json={"email": w.b_admin.email, "password": w.b_admin.password}
        )
        assert r.status_code == 200
    else:  # mfa_setup / recovery_codes: a freshly invited person of B
        _, _, token = await invite_user(app, b)
        await browser.post(
            "/auth/invitations/accept",
            json={"token": token, "password": "uma-senha-bem-longa-e-unica-1"},
        )
        if stage == "recovery_codes":
            secret = (await browser.post("/auth/mfa/setup", headers=csrf(browser))).json()["secret"]
            done = await browser.post(
                "/auth/mfa/activate", json={"code": env.clock.code(secret)}, headers=csrf(browser)
            )
            assert done.status_code == 200
            env.clock.next_step()
    return browser


async def _staff_client(w: World) -> httpx.AsyncClient:
    browser = new_client(w.env.app)
    await login(browser, w.staff, w.env.clock)
    picked = await browser.put(
        "/auth/context", json={"client_id": str(w.seed.tenant_b)}, headers=csrf(browser)
    )
    assert picked.status_code == 200
    return browser


async def _snapshot(owner_factory: Factory, tenant_id: uuid.UUID) -> dict[str, list[str]]:
    """Every row of the client, as JSON text, per table (read as the table owner, under RLS)."""
    async with tenant_session(owner_factory, tenant_id=tenant_id) as db:
        tables = [
            r[0]
            for r in await db.execute(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND column_name = 'tenant_id'"
                    " ORDER BY table_name"
                )
            )
        ]
        result: dict[str, list[str]] = {}
        for table in tables:
            rows = await db.execute(
                text(f"SELECT row_to_json(x)::text FROM {table} x ORDER BY id")  # noqa: S608
            )
            result[table] = [r[0] for r in rows]
        tenant_row = await db.execute(
            text("SELECT row_to_json(x)::text FROM tenants x WHERE id = :t"), {"t": tenant_id}
        )
        result["tenants"] = [r[0] for r in tenant_row]
    return result


def _assert_no_trace_of_a(w: World, spec: RouteSpec, actor: str, response: httpx.Response) -> None:
    body = response.text
    for marker in w.a_markers:
        assert marker not in body, f"{actor} {spec.label}: response contains {marker!r}"


async def _call(
    client: httpx.AsyncClient, spec: RouteSpec, resources: dict[str, str]
) -> httpx.Response:
    body = spec.body()
    return await client.request(spec.method, spec.url(resources), json=body, headers=csrf(client))


def _specs(app: FastAPI) -> list[RouteSpec]:
    return discover(app, RESOURCE_PARAMS)


# --- the sweep --------------------------------------------------------------------------------


async def test_every_route_is_isolated_between_clients(
    world: World, owner_factory: Factory
) -> None:
    w = world
    specs = _specs(w.env.app)
    assert len(specs) >= 25  # guards against the discovery silently finding nothing
    before = await _snapshot(owner_factory, w.seed.tenant_a)

    for spec in specs:
        # B's most privileged user (or no one, for public routes).
        client = await _b_client(w, spec)
        try:
            response = await _call(client, spec, w.a_ids)
        finally:
            await client.aclose()
        label = f"B-admin {spec.label}"
        assert response.status_code < 500, f"{label}: {response.status_code} {response.text}"
        _assert_no_trace_of_a(w, spec, "B-admin", response)
        if spec.path_params:
            assert response.status_code == 404, f"{label}: {response.status_code} {response.text}"
        if getattr(spec.marker, "permission", None) in PLATFORM_ONLY:
            assert response.status_code == 403, f"{label}: {response.status_code}"

    # The Artemisys team, working inside client B. They may read every client by design, but a
    # resource of A is still not found from B's context.
    for spec in specs:
        if spec.marker.kind == "public" or spec.marker.kind == "stage":
            continue
        client = await _staff_client(w)
        try:
            response = await _call(client, spec, w.a_ids)
        finally:
            await client.aclose()
        label = f"staff-in-B {spec.label}"
        assert response.status_code < 500, f"{label}: {response.status_code} {response.text}"
        platform_wide = getattr(spec.marker, "permission", None) in PLATFORM_ONLY
        if not platform_wide:  # /clients legitimately lists every client, A included
            _assert_no_trace_of_a(w, spec, "staff-in-B", response)
        if spec.path_params:
            assert response.status_code == 404, f"{label}: {response.status_code} {response.text}"

    assert await _snapshot(owner_factory, w.seed.tenant_a) == before


async def test_lists_only_contain_the_callers_client(world: World) -> None:
    """A structural check on top of the string search: every tenant id in a list is B's."""
    w = world
    client = new_client(w.env.app)
    try:
        await login(client, w.b_admin, w.env.clock)
        listed = (await client.get("/users", params={"per_page": 50})).json()
        assert listed["items"], "client B should have at least its own admin"
        assert {i["client_id"] for i in listed["items"]} == {str(w.seed.tenant_b)}
        me = (await client.get("/auth/me")).json()
        assert me["context"]["client_id"] == str(w.seed.tenant_b)
    finally:
        await client.aclose()
    ids = set(
        re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", json.dumps(listed)
        )
    )
    assert str(w.seed.tenant_a) not in ids


# --- the sweep itself must fail for uncovered routes ------------------------------------------


@pytest.fixture
def bare_app(db_urls: DbUrls) -> FastAPI:
    return create_app(make_settings(db_urls))


def test_discovery_finds_every_real_route(bare_app: FastAPI) -> None:
    specs = _specs(bare_app)
    labels = {s.label for s in specs}
    assert "GET /health" in labels
    assert "POST /auth/login" in labels
    assert "POST /users/{user_id}/remove-access" in labels
    assert {s.marker.kind for s in specs} == {"public", "stage", "self_service", "permission"}


def test_a_route_without_marker_fails_the_sweep(bare_app: FastAPI) -> None:
    @bare_app.get("/oops")
    async def oops() -> dict[str, str]:
        return {}

    with pytest.raises(Uncovered, match="exactly one route marker"):
        _specs(bare_app)


def test_a_path_parameter_without_a_registry_entry_fails_the_sweep(bare_app: FastAPI) -> None:
    @bare_app.get("/things/{thing_id}", dependencies=[Depends(SelfService())])
    async def thing(thing_id: uuid.UUID) -> dict[str, str]:
        return {}

    with pytest.raises(Uncovered, match="thing_id"):
        _specs(bare_app)


def test_a_body_model_without_examples_fails_the_sweep(bare_app: FastAPI) -> None:
    class NoExamples(BaseModel):
        name: str

    @bare_app.post("/things", dependencies=[Depends(RequireStage("active"))])
    async def create(body: NoExamples) -> None:
        return None

    with pytest.raises(Uncovered, match="examples"):
        _specs(bare_app)


def test_two_markers_or_a_mounted_app_fail_the_sweep(bare_app: FastAPI) -> None:
    @bare_app.get("/both", dependencies=[Depends(PublicRoute()), Depends(SelfService())])
    async def both() -> None:
        return None

    with pytest.raises(Uncovered, match="exactly one route marker"):
        _specs(bare_app)


def test_a_mounted_sub_app_fails_the_sweep(db_urls: DbUrls) -> None:
    app = create_app(make_settings(db_urls))
    app.mount("/shadow", FastAPI())
    with pytest.raises(Uncovered, match="not an APIRoute"):
        _specs(app)


def test_an_agent_route_without_marker_fails_the_sweep(bare_app: FastAPI) -> None:
    @bare_app.post("/agent/oops")
    async def oops() -> None:
        return None

    with pytest.raises(Uncovered, match="exactly one route marker"):
        _specs(bare_app)


def test_an_agent_route_with_a_user_marker_fails_the_sweep(bare_app: FastAPI) -> None:
    @bare_app.get("/agent/oops", dependencies=[Depends(SelfService())])
    async def oops() -> None:
        return None

    with pytest.raises(Uncovered, match="must be a MachineRoute"):
        _specs(bare_app)


def test_a_new_public_agent_route_fails_the_sweep(bare_app: FastAPI) -> None:
    """The list of agent routes that take no token is closed: adding one is a deliberate edit."""

    @bare_app.post("/agent/oops", dependencies=[Depends(PublicRoute())])
    async def oops() -> None:
        return None

    with pytest.raises(Uncovered, match="must be a MachineRoute"):
        _specs(bare_app)


def test_a_user_route_with_the_machine_marker_fails_the_sweep(bare_app: FastAPI) -> None:
    @bare_app.get("/clients/oops", dependencies=[Depends(MachineRoute())])
    async def oops() -> None:
        return None

    with pytest.raises(Uncovered, match="only allowed under /agent/"):
        _specs(bare_app)


def test_a_token_less_agent_path_must_stay_public(bare_app: FastAPI) -> None:
    @bare_app.post("/agent/enroll", dependencies=[Depends(MachineRoute())])
    async def enroll() -> None:
        return None

    with pytest.raises(Uncovered, match="must be a PublicRoute"):
        _specs(bare_app)


def test_require_marker_is_discovered_with_its_permission(db_urls: DbUrls) -> None:
    app = create_app(make_settings(db_urls))
    specs = _specs(app)
    gated = [s for s in specs if isinstance(s.marker, Require)]
    assert gated
    assert all(s.marker.permission for s in gated)  # type: ignore[attr-defined]
