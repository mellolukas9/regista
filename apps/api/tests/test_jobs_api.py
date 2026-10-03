"""Runs as the panel sees them: create, list, detail, cancel, rerun (M3)."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .helpers import Panel, csrf


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _post(
    client: httpx.AsyncClient, path: str, body: dict[str, object] | None = None
) -> httpx.Response:
    return await client.post(path, json=body, headers=csrf(client))


async def make_bot(panel: Panel, tenant_id: uuid.UUID, owner: httpx.AsyncClient) -> dict[str, Any]:
    """A pool (by the client's admin) and a bot in it (by the staff, the only one who can)."""
    pool = await _post(owner, "/pools", {"name": f"pool-{_suffix()}"})
    assert pool.status_code == 201, pool.text
    staff = await panel.staff_in(tenant_id)
    s = _suffix()
    bot = await _post(
        staff,
        "/bots",
        {"name": f"Bot {s}", "package_name": f"pacote_{s}", "pool_id": pool.json()["id"]},
    )
    assert bot.status_code == 201, bot.text
    body: dict[str, Any] = bot.json()
    return body


async def make_machine(panel: Panel, tenant_id: uuid.UUID, pool_id: str) -> uuid.UUID:
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=tenant_id) as db:
        machine_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name) VALUES (:t, :p, :n)"
                    " RETURNING id"
                ),
                {"t": tenant_id, "p": pool_id, "n": f"m-{_suffix()}"},
            )
        ).scalar_one()
    return machine_id


async def set_job(panel: Panel, tenant_id: uuid.UUID, job_id: str, **fields: object) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=tenant_id) as db:
        await db.execute(
            text(f"UPDATE jobs SET {sets} WHERE id = :id"),  # noqa: S608  (test-only columns)
            {"id": job_id, **fields},
        )


async def run(client: httpx.AsyncClient, bot_id: str) -> dict[str, Any]:
    r = await _post(client, "/jobs", {"bot_id": bot_id, "params": {}})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


# --- create -----------------------------------------------------------------------------------


async def test_admin_and_operator_run_a_bot_viewer_cannot(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    job = await run(panel.operator_a, bot["id"])
    assert job["status"] == "pending"
    assert re.fullmatch(r"exec-[0-9a-f]{6}", job["short_code"])
    assert job["trigger"] == "manual"
    assert job["bot_name"] == bot["name"]
    assert job["machine_id"] is None
    assert job["params"] == {}
    assert job["triggered_by"].endswith("@example.com")
    assert job["pool_has_online_machine"] is False

    assert (await run(panel.admin_a, bot["id"]))["status"] == "pending"
    r = await _post(panel.viewer_a, "/jobs", {"bot_id": bot["id"]})
    assert r.status_code == 403


async def test_staff_runs_inside_a_client_and_shows_as_the_team(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    # "All clients" is read-only.
    await panel.staff.put("/auth/context", json={"client_id": None}, headers=csrf(panel.staff))
    r = await _post(panel.staff, "/jobs", {"bot_id": bot["id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "client_context_required"

    staff = await panel.staff_in(panel.tenant_a)
    job = await run(staff, bot["id"])
    seen_by_client = (await panel.admin_a.get(f"/jobs/{job['id']}")).json()
    assert seen_by_client["triggered_by"] == "Equipe Artemisys"


async def test_running_while_another_run_is_active_creates_another_pending(
    panel: Panel,
) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    machine = await make_machine(panel, panel.tenant_a, bot["pool_id"])
    first = await run(panel.admin_a, bot["id"])
    await set_job(panel, panel.tenant_a, first["id"], status="running", machine_id=machine)
    second = await run(panel.admin_a, bot["id"])
    third = await run(panel.admin_a, bot["id"])
    assert second["status"] == third["status"] == "pending"
    assert len({first["short_code"], second["short_code"], third["short_code"]}) == 3
    listed = (await panel.admin_a.get(f"/bots/{bot['id']}")).json()
    assert listed["has_active_run"] is True
    assert listed["recent_statuses"] == ["running", "pending", "pending"]  # oldest first


async def test_create_refusals(panel: Panel) -> None:
    bot_b = await make_bot(panel, panel.tenant_b, panel.admin_b)
    r = await _post(panel.admin_a, "/jobs", {"bot_id": bot_b["id"]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "bot_not_found"
    r = await _post(panel.admin_a, "/jobs", {"bot_id": str(uuid.uuid4())})
    assert r.status_code == 404

    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    big = {"x": "a" * 9000}
    r = await _post(panel.admin_a, "/jobs", {"bot_id": bot["id"], "params": big})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "params_too_large"

    async with tenant_session(panel.env.app.state.session_factory, tenant_id=panel.tenant_a) as db:
        await db.execute(text("UPDATE bots SET is_active = false WHERE id = :b"), {"b": bot["id"]})
    r = await _post(panel.admin_a, "/jobs", {"bot_id": bot["id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bot_inactive"


async def test_production_refuses_runs_until_there_is_a_signed_version(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    job = await run(panel.admin_a, bot["id"])
    await set_job(
        panel, panel.tenant_a, job["id"], status="cancelled", finished_at=datetime.now(UTC)
    )
    app = panel.env.app
    original = app.state.settings
    app.state.settings = original.model_copy(update={"environment": "prod"})
    try:
        r = await _post(panel.admin_a, "/jobs", {"bot_id": bot["id"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "bot_has_no_version"
        r = await _post(panel.admin_a, f"/jobs/{job['id']}/rerun")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "bot_has_no_version"
    finally:
        app.state.settings = original


# --- lists and detail -------------------------------------------------------------------------


async def test_lists_filters_sorting_and_paging(panel: Panel) -> None:
    bot1 = await make_bot(panel, panel.tenant_a, panel.admin_a)
    bot2 = await make_bot(panel, panel.tenant_a, panel.admin_a)
    machine = await make_machine(panel, panel.tenant_a, bot1["pool_id"])
    j1 = await run(panel.admin_a, bot1["id"])
    j2 = await run(panel.admin_a, bot2["id"])
    j3 = await run(panel.admin_a, bot2["id"])
    await set_job(
        panel,
        panel.tenant_a,
        j1["id"],
        status="completed",
        machine_id=machine,
        finished_at=datetime.now(UTC),
    )
    old = datetime.now(UTC) - timedelta(days=20)
    await set_job(panel, panel.tenant_a, j3["id"], created_at=old)

    by_bot = (await panel.admin_a.get(f"/jobs?bot_id={bot2['id']}")).json()
    assert {i["id"] for i in by_bot["items"]} == {j2["id"], j3["id"]}
    assert by_bot["total"] == 2
    done = (await panel.admin_a.get(f"/jobs?bot_id={bot1['id']}&status=completed")).json()
    assert [i["id"] for i in done["items"]] == [j1["id"]]
    assert done["items"][0]["machine_name"] is not None
    seven = (await panel.admin_a.get(f"/jobs?bot_id={bot2['id']}&period=7d")).json()
    assert [i["id"] for i in seven["items"]] == [j2["id"]]
    on_machine = (await panel.admin_a.get(f"/jobs?machine_id={machine}&period=all")).json()
    assert [i["id"] for i in on_machine["items"]] == [j1["id"]]
    code = j2["short_code"]
    found = (await panel.admin_a.get(f"/jobs?q={code[5:]}")).json()
    assert j2["id"] in {i["id"] for i in found["items"]}

    newest = (await panel.admin_a.get(f"/jobs?bot_id={bot2['id']}&period=30d")).json()
    assert [i["id"] for i in newest["items"]] == [j2["id"], j3["id"]]  # newest first by default
    oldest = (await panel.admin_a.get(f"/jobs?bot_id={bot2['id']}&sort=created_at")).json()
    assert [i["id"] for i in oldest["items"]] == [j3["id"], j2["id"]]
    page = (await panel.admin_a.get(f"/jobs?bot_id={bot2['id']}&per_page=10&page=2")).json()
    assert page["items"] == [] and page["total"] == 2

    assert (await panel.admin_a.get("/jobs?status=weird")).status_code == 422
    assert (await panel.admin_a.get("/jobs?sort=drop")).status_code == 422
    assert (await panel.admin_a.get("/jobs?period=forever")).status_code == 422


async def test_each_client_sees_only_its_runs_and_the_staff_sees_all(panel: Panel) -> None:
    bot_a = await make_bot(panel, panel.tenant_a, panel.admin_a)
    bot_b = await make_bot(panel, panel.tenant_b, panel.admin_b)
    job_a = await run(panel.admin_a, bot_a["id"])
    job_b = await run(panel.admin_b, bot_b["id"])

    for client in (panel.admin_a, panel.operator_a, panel.viewer_a):
        ids = {i["id"] for i in (await client.get("/jobs?per_page=50")).json()["items"]}
        assert job_a["id"] in ids and job_b["id"] not in ids
        assert (await client.get(f"/jobs/{job_b['id']}")).status_code == 404
    assert (await panel.admin_b.get(f"/jobs/{job_a['id']}")).status_code == 404

    await panel.staff.put("/auth/context", json={"client_id": None}, headers=csrf(panel.staff))
    everything = (await panel.staff.get("/jobs?per_page=50")).json()
    by_id = {i["id"]: i for i in everything["items"]}
    assert {job_a["id"], job_b["id"]} <= set(by_id)
    assert by_id[job_a["id"]]["client_name"] != by_id[job_b["id"]]["client_name"]


async def test_summary_counts_pending_and_active(panel: Panel) -> None:
    before = (await panel.admin_a.get("/jobs/summary")).json()
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    machine = await make_machine(panel, panel.tenant_a, bot["pool_id"])
    first = await run(panel.admin_a, bot["id"])
    await run(panel.admin_a, bot["id"])
    await set_job(panel, panel.tenant_a, first["id"], status="assigned", machine_id=machine)
    after = (await panel.admin_a.get("/jobs/summary")).json()
    assert after["pending"] == before["pending"] + 1
    assert after["active"] == before["active"] + 2


# --- cancel and rerun -------------------------------------------------------------------------


async def test_cancelling_a_pending_run_is_immediate(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    job = await run(panel.admin_a, bot["id"])
    r = await _post(panel.operator_a, f"/jobs/{job['id']}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["finished_at"] is not None and body["cancel_requested_at"] is not None
    # A finished run cannot be cancelled again.
    again = await _post(panel.operator_a, f"/jobs/{job['id']}/cancel")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "job_not_active"


async def test_cancelling_an_assigned_or_running_run_only_asks_for_it(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    for status in ("assigned", "running"):
        machine = await make_machine(panel, panel.tenant_a, bot["pool_id"])
        job = await run(panel.admin_a, bot["id"])
        await set_job(panel, panel.tenant_a, job["id"], status=status, machine_id=machine)
        r = await _post(panel.admin_a, f"/jobs/{job['id']}/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == status, "the agent, not the panel, ends a run that is on a machine"
        assert body["cancel_requested_at"] is not None and body["finished_at"] is None
        # Asking twice keeps the first request.
        again = (await _post(panel.admin_a, f"/jobs/{job['id']}/cancel")).json()
        assert again["cancel_requested_at"] == body["cancel_requested_at"]


async def test_viewer_and_other_clients_cannot_cancel_or_rerun(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    job = await run(panel.admin_a, bot["id"])
    assert (await _post(panel.viewer_a, f"/jobs/{job['id']}/cancel")).status_code == 403
    assert (await _post(panel.viewer_a, f"/jobs/{job['id']}/rerun")).status_code == 403
    assert (await _post(panel.admin_b, f"/jobs/{job['id']}/cancel")).status_code == 404
    assert (await _post(panel.admin_b, f"/jobs/{job['id']}/rerun")).status_code == 404
    assert (await panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "pending"


async def test_rerun_needs_a_finished_run_and_copies_bot_and_params(panel: Panel) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    job = (
        await _post(panel.admin_a, "/jobs", {"bot_id": bot["id"], "params": {"term": "x"}})
    ).json()
    active = await _post(panel.admin_a, f"/jobs/{job['id']}/rerun")
    assert active.status_code == 409 and active.json()["detail"]["code"] == "job_still_active"

    await set_job(
        panel,
        panel.tenant_a,
        job["id"],
        status="failed",
        finished_at=datetime.now(UTC),
        error_code="robot_failed",
    )
    r = await _post(panel.operator_a, f"/jobs/{job['id']}/rerun")
    assert r.status_code == 201, r.text
    again = r.json()
    assert again["id"] != job["id"] and again["short_code"] != job["short_code"]
    assert again["status"] == "pending"
    assert again["bot_id"] == bot["id"] and again["params"] == {"term": "x"}
    assert again["triggered_by"].startswith("operator-a-")


async def test_audit_records_who_triggered_and_cancelled(
    panel: Panel, owner_factory: async_sessionmaker[AsyncSession]
) -> None:
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    job = await run(panel.operator_a, bot["id"])
    await _post(panel.operator_a, f"/jobs/{job['id']}/cancel")
    # audit_log is write-only for the app role, so it is read as the table owner.
    async with tenant_session(owner_factory, tenant_id=panel.tenant_a) as db:
        actions = [
            r[0]
            for r in await db.execute(
                text("SELECT action FROM audit_log WHERE target_id = :j ORDER BY created_at"),
                {"j": job["id"]},
            )
        ]
    assert actions == ["job.triggered", "job.cancelled"]
