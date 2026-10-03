"""Pools, machines, enrollment keys, revocation and history, as the panel sees them."""

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import DbUrls, Seed, open_env
from .helpers import Env, csrf, new_client, onboard, unique_email

Factory = async_sessionmaker[AsyncSession]


@dataclass
class Browsers:
    env: Env
    seed: Seed
    admin_a: httpx.AsyncClient
    operator_a: httpx.AsyncClient
    viewer_a: httpx.AsyncClient
    admin_b: httpx.AsyncClient
    staff: httpx.AsyncClient  # Artemisys team, "all clients"
    admin_a_id: uuid.UUID


@pytest_asyncio.fixture
async def b(db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID) -> AsyncIterator[Browsers]:
    async with open_env(db_urls) as env:
        clients: dict[str, httpx.AsyncClient] = {}
        accounts = {}
        plan = (
            ("admin_a", seed.tenant_a, "tenant_admin", False),
            ("operator_a", seed.tenant_a, "operator", False),
            ("viewer_a", seed.tenant_a, "viewer", False),
            ("admin_b", seed.tenant_b, "tenant_admin", False),
            ("staff", internal_tenant, "tenant_admin", True),
        )
        for name, tenant, role, platform in plan:
            clients[name] = new_client(env.app)
            accounts[name] = await onboard(
                env.app,
                clients[name],
                tenant,
                env.clock,
                role=role,
                email=unique_email(name.replace("_", "-")),
                platform_admin=platform,
            )
        try:
            yield Browsers(env=env, seed=seed, admin_a_id=accounts["admin_a"].user_id, **clients)
        finally:
            for client in clients.values():
                await client.aclose()


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _post(
    client: httpx.AsyncClient, path: str, body: dict[str, object] | None = None
) -> httpx.Response:
    return await client.post(path, json=body, headers=csrf(client))


async def _pool(client: httpx.AsyncClient, name: str | None = None) -> dict[str, Any]:
    response = await _post(client, "/pools", {"name": name or f"pool-{_suffix()}"})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _writes(pool_id: str, machine: dict[str, str]) -> list[tuple[str, dict[str, object] | None]]:
    """Every state-changing call of the panel, aimed at one machine."""
    machine_id = machine["machine_id"]
    return [
        ("/pools", {"name": "x"}),
        ("/machines", {"name": "x", "pool_id": pool_id, "mode": "service"}),
        (f"/machines/{machine_id}/enrollment-key", None),
        (f"/machines/{machine_id}/revoke", {"confirm_name": machine["name"]}),
    ]


async def _machine(
    client: httpx.AsyncClient, pool_id: str, *, name: str | None = None, mode: str = "service"
) -> dict[str, str]:
    response = await _post(
        client,
        "/machines",
        {"name": name or f"m-{_suffix()}", "pool_id": pool_id, "mode": mode},
    )
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


async def _enrolled(
    factory: Factory, tenant_id: uuid.UUID, pool_id: str, *, status: str = "online"
) -> uuid.UUID:
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        machine_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name, public_key, status,"
                    " credential_version, last_seen_at, enrolled_at, os_info, agent_version)"
                    " VALUES (:t, :p, :n, :k, :s, 1, now(), now(),"
                    " CAST(:os AS jsonb), '0.9.3') RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "p": pool_id,
                    "n": f"m-{_suffix()}",
                    "k": os.urandom(32),
                    "s": status,
                    "os": '{"system": "Windows", "hostname": "PC-01", "evil": "<script>"}',
                },
            )
        ).scalar_one()
    return machine_id


async def _audit_actions(owner_factory: Factory, tenant_id: uuid.UUID, target: str) -> list[str]:
    async with tenant_session(owner_factory, tenant_id=tenant_id) as db:
        rows = await db.execute(
            text("SELECT action FROM audit_log WHERE target_id = :t ORDER BY created_at, id"),
            {"t": target},
        )
        return [r[0] for r in rows]


# --- pools ------------------------------------------------------------------------------------


async def test_pools_are_created_listed_with_counts_and_unique_per_client(b: Browsers) -> None:
    name = f"Pool {_suffix()}"
    pool = await _pool(b.admin_a, name)
    assert pool["name"] == name and pool["kind"] == "on_prem"
    assert pool["machines_total"] == 0 and pool["machines_online"] == 0

    duplicate = await _post(b.admin_a, "/pools", {"name": name.upper()})
    assert (duplicate.status_code, duplicate.json()["detail"]["code"]) == (409, "pool_name_taken")
    assert (await _post(b.admin_b, "/pools", {"name": name})).status_code == 201  # other client
    assert (await _post(b.admin_a, "/pools", {"name": "   "})).status_code == 422

    await _machine(b.admin_a, pool["id"])
    await _enrolled(b.env.app.state.session_factory, b.seed.tenant_a, pool["id"])
    listed = {p["id"]: p for p in (await b.viewer_a.get("/pools")).json()["items"]}
    assert listed[pool["id"]]["machines_total"] == 2
    assert listed[pool["id"]]["machines_online"] == 1


# --- creating a machine and its key -----------------------------------------------------------


async def test_a_machine_starts_pending_with_a_key_that_is_stored_only_as_a_hash(
    b: Browsers, owner_factory: Factory
) -> None:
    pool = await _pool(b.admin_a)
    response = await _post(
        b.admin_a,
        "/machines",
        {"name": f"estacao-{_suffix()}", "pool_id": pool["id"], "mode": "session"},
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    key = created["enrollment_key"]
    assert key.startswith("rgk_") and len(key) > 40

    async with tenant_session(owner_factory, tenant_id=b.seed.tenant_a) as db:
        row = (
            await db.execute(
                text(
                    "SELECT k.key_hash, k.used_at, k.revoked_at, k.created_by,"
                    " k.expires_at - now() AS ttl, m.status, m.mode, m.public_key,"
                    " m.created_by AS mc"
                    " FROM enrollment_keys k JOIN machines m ON m.id = k.machine_id"
                    " WHERE m.id = :m"
                ),
                {"m": created["machine_id"]},
            )
        ).one()
        everything: list[str] = list(
            (await db.execute(text("SELECT row_to_json(k)::text FROM enrollment_keys k")))
            .scalars()
            .all()
        )
    assert bytes(row.key_hash) == hashlib.sha256(key.encode()).digest()
    assert not any(key in r for r in everything), "the plaintext key is in the database"
    assert row.status == "pending" and row.mode == "session" and row.public_key is None
    assert row.used_at is None and row.revoked_at is None
    assert row.created_by == row.mc == b.admin_a_id
    assert 23 * 3600 < row.ttl.total_seconds() <= 24 * 3600

    detail = (await b.admin_a.get(f"/machines/{created['machine_id']}")).json()
    assert detail["status"] == "pending" and detail["key_expires_at"] is not None
    assert "enrollment_key" not in detail and "rgk_" not in str(detail)
    listed = await b.admin_a.get("/machines")
    assert "rgk_" not in listed.text and "enrollment_key" not in listed.text


async def test_machine_creation_is_validated(b: Browsers) -> None:
    pool = await _pool(b.admin_a)
    other_pool = await _pool(b.admin_b)
    name = f"m-{_suffix()}"
    await _machine(b.admin_a, pool["id"], name=name)

    async def create(
        body: dict[str, object], client: httpx.AsyncClient | None = None
    ) -> tuple[int, object]:
        r = await _post(client or b.admin_a, "/machines", body)
        return r.status_code, r.json()["detail"]

    valid = {"name": f"m-{_suffix()}", "pool_id": pool["id"], "mode": "service"}
    for bad_name in ("Upper", "-x", "has space", "a" * 64, "", "ação"):
        assert (await create({**valid, "name": bad_name}))[0] == 422, bad_name
    assert (await create({**valid, "mode": "oneshot"}))[0] == 422  # not in the MVP
    assert (await create({**valid, "mode": "root"}))[0] == 422
    assert (await create({**valid, "extra": 1}))[0] == 422
    assert await create({**valid, "name": name}) == (409, {"code": "machine_name_taken"})
    assert await create({**valid, "pool_id": str(uuid.uuid4())}) == (
        404,
        {"code": "pool_not_found"},
    )
    # A pool of another client does not exist for this one.
    assert await create({**valid, "pool_id": other_pool["id"]}) == (404, {"code": "pool_not_found"})


async def test_a_new_key_replaces_the_live_one_and_changes_nothing_else(
    b: Browsers, owner_factory: Factory
) -> None:
    pool = await _pool(b.admin_a)
    created = await _machine(b.admin_a, pool["id"])
    machine_id = created["machine_id"]

    again = await _post(b.admin_a, f"/machines/{machine_id}/enrollment-key")
    assert again.status_code == 201 and again.headers["cache-control"] == "no-store"
    assert again.json()["enrollment_key"] != created["enrollment_key"]

    async with tenant_session(owner_factory, tenant_id=b.seed.tenant_a) as db:
        keys = (
            await db.execute(
                text(
                    "SELECT key_hash, revoked_at FROM enrollment_keys WHERE machine_id = :m"
                    " ORDER BY created_at"
                ),
                {"m": machine_id},
            )
        ).all()
    assert len(keys) == 2
    assert keys[0].revoked_at is not None and keys[1].revoked_at is None
    assert [bytes(k.key_hash) for k in keys] == [
        hashlib.sha256(created["enrollment_key"].encode()).digest(),
        hashlib.sha256(again.json()["enrollment_key"].encode()).digest(),
    ]

    # An enrolled machine: allowed, and the machine itself is untouched until the key is used.
    enrolled = await _enrolled(b.env.app.state.session_factory, b.seed.tenant_a, pool["id"])
    before = (await b.admin_a.get(f"/machines/{enrolled}")).json()
    replaced = await _post(b.admin_a, f"/machines/{enrolled}/enrollment-key")
    assert replaced.status_code == 201
    after = (await b.admin_a.get(f"/machines/{enrolled}")).json()
    assert before["status"] == after["status"] == "online"
    assert before["last_seen_at"] == after["last_seen_at"]
    assert after["key_expires_at"] is not None  # the live key now exists

    actions = await _audit_actions(owner_factory, b.seed.tenant_a, machine_id)
    assert actions == ["machine.created", "machine.key_generated"]
    async with tenant_session(owner_factory, tenant_id=b.seed.tenant_a) as db:
        meta = (
            await db.execute(
                text(
                    "SELECT actor_id, metadata FROM audit_log WHERE target_id = :t"
                    " AND action = 'machine.key_generated'"
                ),
                {"t": enrolled},
            )
        ).one()
    assert meta.actor_id == b.admin_a_id and meta.metadata["replaces_agent"] is True
    assert "rgk_" not in str(meta.metadata)


# --- revocation -------------------------------------------------------------------------------


async def test_revocation_needs_the_exact_name_and_is_final(
    b: Browsers, owner_factory: Factory
) -> None:
    pool = await _pool(b.admin_a)
    name = f"m-{_suffix()}"
    created = await _machine(b.admin_a, pool["id"], name=name)
    machine_id = created["machine_id"]

    for wrong in (name.upper(), name + " ", "other"):
        r = await _post(b.admin_a, f"/machines/{machine_id}/revoke", {"confirm_name": wrong})
        assert (r.status_code, r.json()["detail"]) == (422, {"code": "name_mismatch"}), wrong
    empty = await _post(b.admin_a, f"/machines/{machine_id}/revoke", {"confirm_name": ""})
    assert empty.status_code == 422  # refused by validation before the name is compared
    assert (await b.admin_a.get(f"/machines/{machine_id}")).json()["status"] == "pending"

    revoked = await _post(b.admin_a, f"/machines/{machine_id}/revoke", {"confirm_name": name})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked" and revoked.json()["revoked_at"] is not None

    async with tenant_session(owner_factory, tenant_id=b.seed.tenant_a) as db:
        live: int = (
            await db.execute(
                text(
                    "SELECT count(*) FROM enrollment_keys WHERE machine_id = :m"
                    " AND used_at IS NULL AND revoked_at IS NULL"
                ),
                {"m": machine_id},
            )
        ).scalar_one()
        revoked_by: uuid.UUID | None = (
            await db.execute(
                text("SELECT revoked_by FROM machines WHERE id = :m"), {"m": machine_id}
            )
        ).scalar_one()
    assert live == 0 and revoked_by == b.admin_a_id

    # Idempotent: no second event and no second audit row.
    again = await _post(b.admin_a, f"/machines/{machine_id}/revoke", {"confirm_name": name})
    assert again.status_code == 200
    events = (await b.admin_a.get(f"/machines/{machine_id}/events")).json()
    assert [e["kind"] for e in events["items"]] == ["revoked"]
    assert await _audit_actions(owner_factory, b.seed.tenant_a, machine_id) == [
        "machine.created",
        "machine.revoked",
    ]

    # Final: no new key, and the name is free for a fresh registration.
    key = await _post(b.admin_a, f"/machines/{machine_id}/enrollment-key")
    assert (key.status_code, key.json()["detail"]) == (409, {"code": "machine_revoked"})
    reused = await _post(
        b.admin_a, "/machines", {"name": name, "pool_id": pool["id"], "mode": "service"}
    )
    assert reused.status_code == 201


# --- reading ----------------------------------------------------------------------------------


async def test_machine_detail_shows_who_registered_it_and_only_known_os_fields(
    b: Browsers,
) -> None:
    pool = await _pool(b.admin_a)
    created = await _machine(b.admin_a, pool["id"])
    detail = (await b.operator_a.get(f"/machines/{created['machine_id']}")).json()
    assert detail["created_by"] and "@" in detail["created_by"]
    assert detail["pool_name"] == pool["name"] and detail["mode"] == "service"

    enrolled = await _enrolled(b.env.app.state.session_factory, b.seed.tenant_a, pool["id"])
    info = (await b.viewer_a.get(f"/machines/{enrolled}")).json()["os_info"]
    assert info == {"system": "Windows", "hostname": "PC-01"}  # the unknown key is dropped

    # Registered by the Artemisys team inside a client: shown as theirs, without the address.
    picked = await b.staff.put(
        "/auth/context", json={"client_id": str(b.seed.tenant_a)}, headers=csrf(b.staff)
    )
    assert picked.status_code == 200
    staff_machine = await _machine(b.staff, pool["id"])
    seen = (await b.admin_a.get(f"/machines/{staff_machine['machine_id']}")).json()
    assert seen["created_by"] == "Equipe Artemisys"


async def test_machine_list_filters_sorts_and_counts_revoked(b: Browsers) -> None:
    pool_1, pool_2 = await _pool(b.admin_a), await _pool(b.admin_a)
    names = [f"a-{_suffix()}", f"b-{_suffix()}", f"c-{_suffix()}"]
    one = await _machine(b.admin_a, pool_1["id"], name=names[0])
    await _machine(b.admin_a, pool_1["id"], name=names[1])
    three = await _machine(b.admin_a, pool_2["id"], name=names[2])
    await _post(b.admin_a, f"/machines/{three['machine_id']}/revoke", {"confirm_name": names[2]})

    only_1 = (await b.viewer_a.get("/machines", params={"pool_id": pool_1["id"]})).json()
    assert [m["name"] for m in only_1["items"]] == names[:2] and only_1["revoked_count"] == 0
    assert only_1["total"] == 2

    hidden = (await b.viewer_a.get("/machines", params={"pool_id": pool_2["id"]})).json()
    assert hidden["items"] == [] and hidden["revoked_count"] == 1
    shown = (
        await b.viewer_a.get(
            "/machines", params={"pool_id": pool_2["id"], "include_revoked": "true"}
        )
    ).json()
    assert [m["name"] for m in shown["items"]] == [names[2]]
    assert shown["items"][0]["status"] == "revoked"

    desc = (
        await b.viewer_a.get("/machines", params={"pool_id": pool_1["id"], "sort": "-name"})
    ).json()
    assert [m["name"] for m in desc["items"]] == names[:2][::-1]
    page = (
        await b.viewer_a.get(
            "/machines", params={"pool_id": pool_1["id"], "per_page": 10, "page": 2}
        )
    ).json()
    assert page["items"] == [] and page["total"] == 2 and page["page"] == 2

    assert (await b.viewer_a.get("/machines", params={"sort": "password"})).status_code == 422
    assert (await b.viewer_a.get("/machines", params={"per_page": 7})).status_code == 422
    assert (await b.viewer_a.get("/machines", params={"pool_id": "nope"})).status_code == 422
    assert one["machine_id"] in str((await b.viewer_a.get("/machines")).json())


async def test_history_is_newest_first_paginated_and_only_for_this_client(
    b: Browsers, owner_factory: Factory
) -> None:
    pool = await _pool(b.admin_a)
    machine_id = str(await _enrolled(b.env.app.state.session_factory, b.seed.tenant_a, pool["id"]))
    async with tenant_session(owner_factory, tenant_id=b.seed.tenant_a) as db:
        for i, kind in enumerate(("enrolled", "first_signal", "went_offline", "came_back")):
            await db.execute(
                text(
                    "INSERT INTO machine_events (tenant_id, machine_id, kind, created_at)"
                    " VALUES (:t, :m, :k, now() - make_interval(mins => :age))"
                ),
                {"t": b.seed.tenant_a, "m": machine_id, "k": kind, "age": 10 - i},
            )
    body = (await b.viewer_a.get(f"/machines/{machine_id}/events", params={"per_page": 10})).json()
    assert [e["kind"] for e in body["items"]] == [
        "came_back",
        "went_offline",
        "first_signal",
        "enrolled",
    ]
    assert body["total"] == 4

    paged = (
        await b.viewer_a.get(f"/machines/{machine_id}/events", params={"per_page": 10, "page": 2})
    ).json()
    assert paged["items"] == []
    # Another client sees neither the machine nor its history.
    for path in (f"/machines/{machine_id}", f"/machines/{machine_id}/events"):
        r = await b.admin_b.get(path)
        assert (r.status_code, r.json()["detail"]) == (404, {"code": "machine_not_found"})
    assert (await b.viewer_a.get(f"/machines/{uuid.uuid4()}/events")).status_code == 404


async def test_summary_counts_machines_without_signal(b: Browsers) -> None:
    pool = await _pool(b.admin_a)
    factory = b.env.app.state.session_factory
    await _enrolled(factory, b.seed.tenant_a, pool["id"], status="online")
    await _enrolled(factory, b.seed.tenant_a, pool["id"], status="offline")
    await _machine(b.admin_a, pool["id"])  # pending: not "sem sinal"

    mine = (await b.operator_a.get("/machines/summary")).json()
    assert mine["no_signal"] >= 1 and mine["online"] >= 1 and mine["total"] >= 3
    assert mine["clients"] == []  # the per-client breakdown is only for "all clients"

    everyone = (await b.staff.get("/machines/summary")).json()
    by_client = {c["client_id"]: c for c in everyone["clients"]}
    assert str(b.seed.tenant_a) in by_client
    assert by_client[str(b.seed.tenant_a)]["no_signal"] == mine["no_signal"]
    assert everyone["no_signal"] >= mine["no_signal"]
    # A client with no machine without signal is not listed.
    assert all(c["no_signal"] > 0 for c in everyone["clients"])


# --- who may do what --------------------------------------------------------------------------


async def test_everyone_reads_but_only_admins_change_and_all_clients_is_read_only(
    b: Browsers,
) -> None:
    pool = await _pool(b.admin_a)
    machine = await _machine(b.admin_a, pool["id"])
    machine_id = machine["machine_id"]

    for client in (b.operator_a, b.viewer_a):
        for path in ("/pools", "/machines", "/machines/summary", f"/machines/{machine_id}"):
            assert (await client.get(path)).status_code == 200, path
        for path, body in _writes(pool["id"], machine):
            r = await _post(client, path, body)
            assert (r.status_code, r.json()["detail"]) == (403, {"code": "forbidden"}), path

    # The Artemisys team in "all clients" may read everything and write nothing.
    seen = (await b.staff.get("/machines")).json()
    assert machine_id in str(seen) and {"client_name"} <= set(seen["items"][0])
    for path, body in _writes(pool["id"], machine):
        r = await _post(b.staff, path, body)
        assert (r.status_code, r.json()["detail"]) == (409, {"code": "client_context_required"})
    # The refused writes changed nothing.
    assert (await b.admin_a.get(f"/machines/{machine_id}")).json()["status"] == "pending"
