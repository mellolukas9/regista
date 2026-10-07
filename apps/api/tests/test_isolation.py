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
from regista_api.machines.agent import audience
from regista_api.main import create_app

from .agent_helpers import AgentSim
from .conftest import DbUrls, Seed, make_settings, open_env
from .helpers import Account, Env, csrf, invite_user, login, new_client, onboard, unique_email
from .isolation import RouteSpec, Uncovered, discover
from .test_machine_token import _rebuild

Factory = async_sessionmaker[AsyncSession]

# Path parameters that name a resource owned by a client, and where to find an A example.
RESOURCE_PARAMS = {
    "user_id",
    "session_id",
    "machine_id",
    "pool_id",
    "bot_id",
    "job_id",
    "artifact_id",
    "version_id",
    "bot_version_id",
}


@dataclass
class World:
    env: Env
    seed: Seed
    internal: uuid.UUID
    b_admin: Account
    staff: Account
    a_ids: dict[str, str]
    a_markers: list[str]  # strings that must never show up in anything B or staff-in-B receives
    a_used_key: str  # an enrollment key of A that is already burned
    a_agent: AgentSim  # an enrolled machine of A, with a real key pair
    b_agent: AgentSim  # an enrolled machine of B, with a valid access token


@pytest_asyncio.fixture
async def world(db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID) -> AsyncIterator[World]:
    async with open_env(
        db_urls,
        rate_login_email_per_15min=1_000_000,
        rate_mfa_per_5min=1_000_000,
        lockout_threshold=1_000_000,
    ) as env:
        a_browser = new_client(env.app)
        a_admin = await onboard(
            env.app,
            a_browser,
            seed.tenant_a,
            env.clock,
            role="tenant_admin",
            email=unique_email("leak-admin"),
        )
        agent_client = new_client(env.app)  # no cookies: an agent never has a session
        aud = audience(env.app.state.settings.api_public_url)
        a_machines = await _client_a_machines(a_browser, agent_client, aud)
        a_target = await onboard(
            env.app, new_client(env.app), seed.tenant_a, env.clock, email=unique_email("leak-user")
        )
        b_browser = new_client(env.app)
        b_admin = await onboard(env.app, b_browser, seed.tenant_b, env.clock, role="tenant_admin")
        b_agent = await _enrolled_agent(b_browser, agent_client, aud)
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
            bot_name = f"bot-secreto-{uuid.uuid4().hex[:8]}"
            bot_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO bots (tenant_id, pool_id, name, package_name)"
                        " VALUES (:t, :p, :n, :k) RETURNING id"
                    ),
                    {
                        "t": seed.tenant_a,
                        "p": a_machines.pool_id,
                        "n": bot_name,
                        "k": f"pacote_secreto_{uuid.uuid4().hex[:8]}",
                    },
                )
            ).scalar_one()
            version_sha = uuid.uuid4().hex + uuid.uuid4().hex
            version_key = f"tenants/{seed.tenant_a}/bots/{bot_id}/versions/{uuid.uuid4()}.rgpkg"
            version_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO bot_versions (tenant_id, bot_id, version, package_sha256,"
                        " size_bytes, signature, key_id, manifest, storage_key, status,"
                        " upload_expires_at, published_at, release_note)"
                        " VALUES (:t, :b, '9.8.7', :sha, 10, :sig, '0123456789abcdef',"
                        " CAST(:m AS jsonb), :k, 'published', now(), now(), :n) RETURNING id"
                    ),
                    {
                        "t": seed.tenant_a,
                        "b": bot_id,
                        "sha": version_sha,
                        "sig": b"\x01" * 64,
                        "m": '{"python": "3.13.5", "playwright": null, "chromium_revision": null}',
                        "k": version_key,
                        "n": "nota-secreta-de-a",
                    },
                )
            ).scalar_one()
            job_code = f"exec-{uuid.uuid4().hex[:6]}"
            job_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO jobs (tenant_id, bot_id, pool_id, short_code, params)"
                        " VALUES (:t, :b, :p, :c, CAST(:x AS jsonb)) RETURNING id"
                    ),
                    {
                        "t": seed.tenant_a,
                        "b": bot_id,
                        "p": a_machines.pool_id,
                        "c": job_code,
                        "x": '{"segredo": "param-secreto-de-a"}',
                    },
                )
            ).scalar_one()
            artifact_key = f"tenants/{seed.tenant_a}/jobs/{job_id}/{uuid.uuid4()}.png"
            artifact_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO artifacts (tenant_id, job_id, storage_key, content_type,"
                        " size_bytes, uploaded_at) VALUES (:t, :j, :k, 'image/png', 10, now())"
                        " RETURNING id"
                    ),
                    {"t": seed.tenant_a, "j": job_id, "k": artifact_key},
                )
            ).scalar_one()
        try:
            yield World(
                env=env,
                seed=seed,
                internal=internal_tenant,
                b_admin=b_admin,
                staff=staff,
                a_ids={
                    "user_id": str(target_id),
                    "session_id": str(session_id),
                    "machine_id": a_machines.pending_id,
                    "pool_id": a_machines.pool_id,
                    "bot_id": str(bot_id),
                    "job_id": str(job_id),
                    "artifact_id": str(artifact_id),
                    "version_id": str(version_id),
                    "bot_version_id": str(version_id),
                },
                a_markers=[
                    str(seed.tenant_a),
                    "Tenant A",
                    a_admin.email,
                    a_target.email,
                    str(target_id),
                    str(session_id),
                    str(bot_id),
                    bot_name,
                    str(job_id),
                    job_code,
                    str(artifact_id),
                    artifact_key,
                    str(version_id),
                    version_sha,
                    version_key,
                    "nota-secreta-de-a",
                    "param-secreto-de-a",
                    *a_machines.markers,
                ],
                a_used_key=a_machines.used_key,
                a_agent=a_machines.agent,
                b_agent=b_agent,
            )
        finally:
            await a_browser.aclose()
            await b_browser.aclose()
            await agent_client.aclose()


@dataclass
class AMachines:
    pool_id: str
    pending_id: str
    used_key: str  # the enrollment key A's enrolled machine already used
    agent: AgentSim
    markers: list[str]


async def _enrolled_agent(
    browser: httpx.AsyncClient, agent_client: httpx.AsyncClient, aud: str
) -> AgentSim:
    """A pool and a machine of the browser's client, enrolled by a simulated agent that then has
    a valid access token."""
    suffix = uuid.uuid4().hex[:8]
    pool = await browser.post("/pools", json={"name": f"pool-{suffix}"}, headers=csrf(browser))
    assert pool.status_code == 201, pool.text
    created = await browser.post(
        "/machines",
        json={"name": f"m-{suffix}", "pool_id": pool.json()["id"], "mode": "service"},
        headers=csrf(browser),
    )
    assert created.status_code == 201, created.text
    agent = AgentSim(agent_client, aud)
    enrolled = await agent.enroll(created.json()["enrollment_key"])
    assert enrolled.status_code == 200, enrolled.text
    await agent.login()
    return agent


async def _client_a_machines(
    browser: httpx.AsyncClient, agent_client: httpx.AsyncClient, aud: str
) -> AMachines:
    """Client A's pool and machines: one pending with a live key, one enrolled with history."""
    suffix = uuid.uuid4().hex[:8]
    pool_name = f"pool-secreto-{suffix}"
    pool = await browser.post("/pools", json={"name": pool_name}, headers=csrf(browser))
    assert pool.status_code == 201, pool.text
    pool_id = pool.json()["id"]
    pending_name = f"maquina-pendente-{suffix}"
    created = await browser.post(
        "/machines",
        json={"name": pending_name, "pool_id": pool_id, "mode": "service"},
        headers=csrf(browser),
    )
    assert created.status_code == 201, created.text
    pending = created.json()

    # The enrolled one goes through the real protocol, so it has a real key pair to be
    # impersonated with, and a version string that must never reach B.
    enrolled_name = f"maquina-ativa-{suffix}"
    other = await browser.post(
        "/machines",
        json={"name": enrolled_name, "pool_id": pool_id, "mode": "service"},
        headers=csrf(browser),
    )
    assert other.status_code == 201, other.text
    agent = AgentSim(agent_client, aud)
    used_key = other.json()["enrollment_key"]
    enrolled = await agent.enroll(used_key, agent_version="0.0.0-leak")
    assert enrolled.status_code == 200, enrolled.text
    return AMachines(
        pool_id=pool_id,
        pending_id=pending["machine_id"],
        used_key=used_key,
        agent=agent,
        markers=[
            pool_id,
            pool_name,
            pending["machine_id"],
            pending_name,
            pending["enrollment_key"],
            used_key,
            agent.machine_id,
            enrolled_name,
            "0.0.0-leak",
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
                    "SELECT c.table_name FROM information_schema.columns c"
                    " JOIN pg_class k ON k.oid = format('public.%I', c.table_name)::regclass"
                    " WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'"
                    # Partitions are read through their parent: listing both would count twice.
                    " AND NOT k.relispartition ORDER BY c.table_name"
                )
            )
        ]
        result: dict[str, list[str]] = {}
        for table in tables:
            rows = await db.execute(
                text(f"SELECT row_to_json(x)::text FROM {table} x ORDER BY 1")  # noqa: S608
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
        if spec.marker.kind == "machine":
            # A person's session is not a machine: 401, whatever the route (the machine side is
            # covered by `test_a_machine_of_b_reaches_nothing_of_a`).
            assert response.status_code == 401, f"{label}: {response.status_code} {response.text}"
        elif getattr(spec.marker, "permission", None) in PLATFORM_ONLY:
            # Not theirs to call at all: refused before the route looks at any id.
            assert response.status_code == 403, f"{label}: {response.status_code}"
        elif spec.path_params:
            assert response.status_code == 404, f"{label}: {response.status_code} {response.text}"

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
        if spec.marker.kind == "machine":
            assert response.status_code == 401, f"{label}: {response.status_code} {response.text}"
        elif spec.path_params:
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


# --- machine credentials -----------------------------------------------------------------------


def _machine_specs(app: FastAPI) -> list[RouteSpec]:
    specs = [s for s in _specs(app) if s.marker.kind == "machine"]
    assert specs, "no machine route was discovered"
    return specs


async def test_a_machine_of_b_reaches_nothing_of_a(world: World, owner_factory: Factory) -> None:
    w = world
    before = await _snapshot(owner_factory, w.seed.tenant_a)
    for spec in _machine_specs(w.env.app):
        r = await w.b_agent.client.request(
            spec.method,
            spec.url(w.a_ids),
            json=spec.body(),
            headers={"Authorization": f"Bearer {w.b_agent.token}"},
        )
        assert r.status_code < 500, f"B-machine {spec.label}: {r.status_code} {r.text}"
        _assert_no_trace_of_a(w, spec, "B-machine", r)
        if spec.path_params:
            # A job, an artifact or a machine of A: not found, exactly like one that never existed.
            assert r.status_code == 404, f"B-machine {spec.label}: {r.status_code} {r.text}"
    assert await _snapshot(owner_factory, w.seed.tenant_a) == before


async def test_user_routes_refuse_a_machine_token(world: World) -> None:
    """A machine token is not a session: every route that needs a person says 401."""
    w = world
    checked = 0
    for spec in _specs(w.env.app):
        if spec.marker.kind in ("public", "machine"):
            continue
        r = await w.b_agent.client.request(
            spec.method,
            spec.url(w.a_ids),
            json=spec.body(),
            headers={"Authorization": f"Bearer {w.b_agent.token}"},
        )
        assert r.status_code == 401, f"{spec.label}: {r.status_code} {r.text}"
        checked += 1
    assert checked >= 20


async def test_machine_routes_refuse_a_user_session(world: World) -> None:
    """The mirror image: a logged-in person (cookie and CSRF) is not a machine."""
    w = world
    for spec in _machine_specs(w.env.app):
        for actor in ("B-admin", "staff-in-B"):
            client = await _b_client(w, spec) if actor == "B-admin" else await _staff_client(w)
            try:
                r = await _call(client, spec, w.a_ids)
            finally:
                await client.aclose()
            assert r.status_code == 401, f"{actor} {spec.label}: {r.status_code} {r.text}"


async def test_b_cannot_use_a_machine_of_a(world: World, owner_factory: Factory) -> None:
    w = world
    a, b = w.a_agent, w.b_agent
    unauthorised = (401, {"detail": {"code": "invalid_credentials"}})

    # A's agent asks for a challenge; B only knows the machine id and the nonce it was shown.
    nonce = (await a.challenge()).json()["nonce"]
    before = await _snapshot(owner_factory, w.seed.tenant_a)

    # B signs with its own key, for A's machine and A's nonce.
    for label, signature in {
        "signed for A's id": b.sign(nonce, machine_id=a.machine_id),
        "signed for B's id": b.sign(nonce),
    }.items():
        r = await b.exchange(nonce, signature, machine_id=a.machine_id)
        assert (r.status_code, r.json()) == unauthorised, label
    assert await _snapshot(owner_factory, w.seed.tenant_a) == before  # the nonce was not burned
    assert (await a.exchange(nonce, a.sign(nonce))).status_code == 200  # and A still logs in

    # A's id with a made-up nonce.
    fake = "bm9uY2U="
    r = await b.exchange(fake, b.sign(fake, machine_id=a.machine_id), a.machine_id)
    assert (r.status_code, r.json()) == unauthorised


async def test_the_used_key_of_a_machine_of_a_is_worthless_to_b(world: World) -> None:
    w = world
    assert w.a_used_key.startswith("rgk_")
    r = await w.b_agent.client.post(
        "/agent/enroll",
        json=w.b_agent.enroll_body(w.a_used_key),
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert (r.status_code, r.json()) == (401, {"detail": {"code": "invalid_enrollment_key"}})


async def test_a_token_with_another_tenant_inside_is_refused(world: World) -> None:
    """The tenant of an agent request comes from the signed token: editing it breaks the MAC."""
    w = world
    changes: list[dict[str, object]] = [
        {"tid": str(w.seed.tenant_a)},
        {"mid": w.a_agent.machine_id},
    ]
    for change in changes:
        r = await w.b_agent.heartbeat(_rebuild(w.b_agent.token, change))
        assert (r.status_code, r.json()["detail"]["code"]) == (401, "not_authenticated"), change


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
    assert {
        "GET /pools",
        "POST /pools",
        "GET /machines",
        "POST /machines",
        "GET /machines/summary",
        "GET /machines/{machine_id}",
        "GET /machines/{machine_id}/events",
        "POST /machines/{machine_id}/enrollment-key",
        "POST /machines/{machine_id}/revoke",
    } <= labels
    assert {s.marker.kind for s in specs} == {
        "public",
        "stage",
        "self_service",
        "permission",
        "machine",
    }
    assert {
        "POST /agent/enroll",
        "POST /agent/challenge",
        "POST /agent/token",
        "POST /agent/heartbeat",
    } <= labels


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
