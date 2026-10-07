"""Bots as the panel sees them: registering is Artemisys-only, reading follows the client."""

import uuid
from typing import Any

import httpx

from .helpers import Panel, csrf


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _post(client: httpx.AsyncClient, path: str, body: dict[str, object]) -> httpx.Response:
    return await client.post(path, json=body, headers=csrf(client))


async def _pool(client: httpx.AsyncClient) -> str:
    r = await _post(client, "/pools", {"name": f"pool-{_suffix()}"})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _body(pool_id: str, **overrides: object) -> dict[str, object]:
    s = _suffix()
    return {
        "name": f"Bot {s}",
        "package_name": f"pacote_{s}",
        "pool_id": pool_id,
        **overrides,
    }


async def test_only_the_staff_registers_a_bot_inside_one_client(panel: Panel) -> None:
    pool = await _pool(panel.admin_a)
    # Client users, whatever the role, are refused.
    for client in (panel.admin_a, panel.operator_a, panel.viewer_a):
        r = await _post(client, "/bots", _body(pool))
        assert r.status_code == 403, r.text
    # The staff must pick a client first ("all clients" is read-only).
    r = await _post(panel.staff, "/bots", _body(pool))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "client_context_required"

    staff = await panel.staff_in(panel.tenant_a)
    r = await _post(staff, "/bots", _body(pool, description="  Pesquisa e captura  "))
    assert r.status_code == 201, r.text
    bot = r.json()
    assert bot["pool_id"] == pool
    assert bot["description"] == "Pesquisa e captura"
    assert bot["recent_statuses"] == []
    assert bot["last_run"] is None
    assert bot["has_active_run"] is False


async def test_a_bot_needs_a_pool_of_the_same_client(panel: Panel) -> None:
    pool_b = await _pool(panel.admin_b)
    staff = await panel.staff_in(panel.tenant_a)
    r = await _post(staff, "/bots", _body(pool_b))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "pool_not_found"


async def test_names_and_package_names_are_validated_and_unique_per_client(panel: Panel) -> None:
    pool = await _pool(panel.admin_a)
    staff = await panel.staff_in(panel.tenant_a)
    body = _body(pool)
    assert (await _post(staff, "/bots", body)).status_code == 201

    same_name = _body(pool, name=str(body["name"]).upper())
    r = await _post(staff, "/bots", same_name)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bot_name_taken"

    same_package = _body(pool, package_name=body["package_name"])
    r = await _post(staff, "/bots", same_package)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "package_name_taken"

    for bad in ("Bad-Name", "9starts_with_digit", "../escape", "a" * 64, "", "com espaço"):
        r = await _post(staff, "/bots", _body(pool, package_name=bad))
        assert r.status_code == 422, bad
    r = await _post(staff, "/bots", _body(pool, name="   "))
    assert r.status_code == 422

    # The same names are free in another client.
    pool_b = await _pool(panel.admin_b)
    staff_b = await panel.staff_in(panel.tenant_b)
    r = await _post(
        staff_b, "/bots", _body(pool_b, name=body["name"], package_name=body["package_name"])
    )
    assert r.status_code == 201, r.text


async def test_lists_and_details_follow_the_client(panel: Panel) -> None:
    pool_a = await _pool(panel.admin_a)
    pool_b = await _pool(panel.admin_b)
    staff = await panel.staff_in(panel.tenant_a)
    bot_a = (await _post(staff, "/bots", _body(pool_a, name="Controle de Acordos"))).json()
    staff = await panel.staff_in(panel.tenant_b)
    bot_b = (await _post(staff, "/bots", _body(pool_b, name="Controle do outro cliente"))).json()

    for client in (panel.admin_a, panel.operator_a, panel.viewer_a):
        # Filtered by name: the test database is shared, and the first page is not all of it.
        listed: dict[str, Any] = (await client.get("/bots", params={"q": "Controle"})).json()
        ids = {i["id"] for i in listed["items"]}
        assert bot_a["id"] in ids and bot_b["id"] not in ids
        assert (await client.get(f"/bots/{bot_a['id']}")).status_code == 200
        assert (await client.get(f"/bots/{bot_b['id']}")).status_code == 404
    assert (await panel.admin_b.get(f"/bots/{bot_a['id']}")).status_code == 404

    detail = (await panel.admin_a.get(f"/bots/{bot_a['id']}")).json()
    assert detail["machines_total"] == 0 and detail["machines_online"] == 0
    assert detail["runs_30d"] == 0 and detail["success_rate_30d"] is None

    # "All clients": both, each with its client name.
    await panel.staff.put("/auth/context", json={"client_id": None}, headers=csrf(panel.staff))
    everything = (await panel.staff.get("/bots?per_page=50")).json()
    by_id = {i["id"]: i for i in everything["items"]}
    assert {bot_a["id"], bot_b["id"]} <= set(by_id)
    assert by_id[bot_a["id"]]["client_name"] != by_id[bot_b["id"]]["client_name"]


async def test_search_sort_and_paging_run_on_the_server(panel: Panel) -> None:
    pool = await _pool(panel.admin_a)
    staff = await panel.staff_in(panel.tenant_a)
    tag = _suffix()
    for n in ("zeta", "alfa", "meio"):
        r = await _post(staff, "/bots", _body(pool, name=f"{n} {tag}"))
        assert r.status_code == 201

    found = (await panel.admin_a.get(f"/bots?q={tag}&sort=name")).json()
    assert [i["name"].split()[0] for i in found["items"]] == ["alfa", "meio", "zeta"]
    desc = (await panel.admin_a.get(f"/bots?q={tag}&sort=-name")).json()
    assert [i["name"].split()[0] for i in desc["items"]] == ["zeta", "meio", "alfa"]
    page = (await panel.admin_a.get(f"/bots?q={tag}&per_page=10&page=2")).json()
    assert page["total"] == 3 and page["items"] == []

    # The user's `%` and `_` are not wildcards.
    assert (await panel.admin_a.get("/bots?q=%25")).json()["total"] == 0
    assert (await panel.admin_a.get("/bots?sort=drop table")).status_code == 422
    assert (await panel.admin_a.get("/bots?per_page=7")).status_code == 422
