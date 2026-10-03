"""Role permissions are enforced on the server, route by route (design-system.md, section 4).

`MATRIX` is a literal copy of the rows of that table that exist in M1. It is compared with the
code (`ROLE_PERMISSIONS`) and then with the behaviour of every route that declares a permission,
found by the same automatic discovery used by the isolation sweep.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest_asyncio

from regista_api.auth.deps import Require
from regista_api.auth.permissions import Permission, permissions_for

from .conftest import DbUrls, Seed, open_env
from .helpers import Env, csrf, new_client, onboard
from .isolation import RouteSpec, discover

ACTORS = ("artemisys", "tenant_admin", "operator", "viewer")

# Rows of the permission table (design-system.md section 4) that M1 implements.
#   "Ver todos os clientes, seletor de cliente, tela Clientes"
#   "Novo cliente"
#   "Usuários: Convidar, Mudar papel, Encerrar sessões, Remover acesso (e Reenviar convite)"
MATRIX: dict[Permission, dict[str, bool]] = {
    Permission.CLIENTS_VIEW_ALL: {
        "artemisys": True,
        "tenant_admin": False,
        "operator": False,
        "viewer": False,
    },
    Permission.CLIENTS_CREATE: {
        "artemisys": True,
        "tenant_admin": False,
        "operator": False,
        "viewer": False,
    },
    Permission.USERS_MANAGE: {
        "artemisys": True,
        "tenant_admin": True,
        "operator": False,
        "viewer": False,
    },
}

# State-changing routes that need no permission, by design.
PUBLIC_MUTATING = {"/auth/login", "/auth/invitations/inspect", "/auth/invitations/accept"}


def _can(permission: Permission, actor: str) -> bool:
    return permission in permissions_for(
        role="tenant_admin" if actor == "artemisys" else actor,
        is_platform_admin=actor == "artemisys",
    )


def test_matrix_covers_every_permission_and_matches_the_code() -> None:
    assert set(MATRIX) == set(Permission), "a permission has no row in the matrix"
    for permission, row in MATRIX.items():
        assert set(row) == set(ACTORS)
        for actor, allowed in row.items():
            assert _can(permission, actor) is allowed, f"{actor} / {permission}"


@dataclass
class Actors:
    env: Env
    browsers: dict[str, httpx.AsyncClient]
    specs: list[RouteSpec]


@pytest_asyncio.fixture
async def actors(db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID) -> AsyncIterator[Actors]:
    async with open_env(db_urls) as env:
        browsers: dict[str, httpx.AsyncClient] = {}
        for actor in ACTORS:
            browser = new_client(env.app)
            if actor == "artemisys":
                await onboard(
                    env.app,
                    browser,
                    internal_tenant,
                    env.clock,
                    role="tenant_admin",
                    platform_admin=True,
                )
            else:
                await onboard(env.app, browser, seed.tenant_a, env.clock, role=actor)
            browsers[actor] = browser
        try:
            yield Actors(env, browsers, discover(env.app, {"user_id", "session_id"}))
        finally:
            for browser in browsers.values():
                await browser.aclose()


async def test_every_permission_gated_route_obeys_the_matrix(actors: Actors) -> None:
    gated = [s for s in actors.specs if isinstance(s.marker, Require)]
    assert {s.marker.permission for s in gated if isinstance(s.marker, Require)} == set(Permission)

    unknown = {"user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4())}
    for spec in gated:
        assert isinstance(spec.marker, Require)
        for actor in ACTORS:
            client = actors.browsers[actor]
            response = await client.request(
                spec.method, spec.url(unknown), json=spec.body(), headers=csrf(client)
            )
            allowed = MATRIX[spec.marker.permission][actor]
            label = f"{actor} {spec.label}"
            if allowed:
                assert response.status_code != 403, f"{label}: {response.text}"
                assert response.status_code < 500, f"{label}: {response.text}"
            else:
                assert response.status_code == 403, f"{label}: {response.status_code}"
                assert response.json()["detail"] == {"code": "forbidden"}, label


async def test_self_service_routes_are_open_to_every_role(actors: Actors) -> None:
    for actor in ACTORS:
        client = actors.browsers[actor]
        for path in ("/auth/me", "/account/sessions"):
            assert (await client.get(path)).status_code == 200, f"{actor} {path}"


def test_every_mutating_route_declares_who_may_call_it(actors: Actors) -> None:
    """A route that changes data must be permission-gated, self-service, a login step or one of
    the few public entry points. Anything else is a hole."""
    for spec in actors.specs:
        if not spec.mutates:
            continue
        kind = spec.marker.kind
        if kind == "public":
            assert spec.path in PUBLIC_MUTATING, f"{spec.label}: public but mutating"
        elif kind == "stage":
            assert spec.path.startswith("/auth/"), f"{spec.label}: partial-session route"
        else:
            assert kind in ("permission", "self_service"), spec.label


async def test_the_session_carries_the_role_of_the_person(actors: Actors) -> None:
    for actor in ("tenant_admin", "operator", "viewer"):
        me = (await actors.browsers[actor].get("/auth/me")).json()
        assert me["role"] == actor
        assert me["is_platform_admin"] is False
    staff = (await actors.browsers["artemisys"].get("/auth/me")).json()
    assert staff["is_platform_admin"] is True
