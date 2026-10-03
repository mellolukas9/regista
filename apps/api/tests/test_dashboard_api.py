"""The dashboard's numbers, per client and consolidated (M3)."""

from datetime import UTC, datetime

from sqlalchemy import text

from regista_api.core.db import tenant_session

from .helpers import csrf
from .jobs_helpers import Rig, enrolled_agent, make_bot, run, set_job


async def _finish(rig: Rig, job_id: str, status: str) -> None:
    await set_job(
        rig.panel, rig.panel.tenant_a, job_id, status=status, finished_at=datetime.now(UTC)
    )


async def test_a_client_sees_its_own_numbers_only(rig: Rig) -> None:
    panel = rig.panel
    before = (await panel.admin_a.get("/dashboard")).json()

    done = await run(panel.admin_a, rig.bot["id"])
    failed = await run(panel.admin_a, rig.bot["id"])
    await run(panel.admin_a, rig.bot["id"])
    await _finish(rig, done["id"], "completed")
    await _finish(rig, failed["id"], "failed")

    bot_b = await make_bot(panel, panel.tenant_b, panel.admin_b)
    await run(panel.admin_b, bot_b["id"])

    after = (await panel.admin_a.get("/dashboard")).json()
    assert after["all_clients"] is False and after["clients_active"] is None
    assert after["runs_total"] == before["runs_total"] + 3, "client B's run is not counted"
    assert after["runs_finished"] == before["runs_finished"] + 2
    assert after["runs_pending"] == before["runs_pending"] + 1
    assert after["success_rate"] is not None and 0 <= after["success_rate"] <= 1
    assert after["by_client"] == []

    today = after["daily"][-1]
    assert len(after["daily"]) == 14
    assert today["completed"] >= before["daily"][-1]["completed"] + 1
    assert today["failed"] >= before["daily"][-1]["failed"] + 1

    assert {r["client_name"] for r in after["latest_runs"]} == {
        after["latest_runs"][0]["client_name"]
    }
    assert len(after["latest_runs"]) <= 5
    for viewer_like in (panel.operator_a, panel.viewer_a):
        assert (await viewer_like.get("/dashboard")).status_code == 200


async def test_machines_pending_runs_and_silent_machines(rig: Rig) -> None:
    panel = rig.panel
    other = await enrolled_agent(
        panel, panel.tenant_a, panel.admin_a, rig.bot["pool_id"], rig.agent_client
    )
    job = await run(panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).json()["job_id"] == job["id"]
    await rig.agent.job_call(job["id"], "start")
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=panel.tenant_a) as db:
        await db.execute(
            text(
                "UPDATE machines SET status = 'offline', last_seen_at = now() - interval '1 hour'"
                " WHERE id = :m"
            ),
            {"m": other.machine_id},
        )
    waiting = await run(panel.admin_a, rig.bot["id"])

    data = (await panel.admin_a.get("/dashboard")).json()
    by_id = {m["id"]: m for m in data["machines"]}
    assert by_id[rig.agent.machine_id]["current_job"]["short_code"] == job["short_code"]
    assert by_id[other.machine_id]["status"] == "offline"
    silent = {s["id"]: s for s in data["silent"]}
    assert silent[other.machine_id]["other_online"] is not None, "another machine of the pool is up"
    assert waiting["id"] in {j["id"] for j in data["pending_runs"]}
    assert data["machines_no_signal"] >= 1
    assert any(a["kind"] == "machine_offline" for a in data["attention"])


async def test_a_run_waiting_too_long_needs_attention(rig: Rig) -> None:
    panel = rig.panel
    job = await run(panel.admin_a, rig.bot["id"])
    await set_job(
        panel,
        panel.tenant_a,
        job["id"],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    data = (await panel.admin_a.get("/dashboard")).json()
    stuck = [a for a in data["attention"] if a["kind"] == "job_pending"]
    assert job["short_code"] in {a["label"] for a in stuck}


async def test_the_team_sees_all_clients_consolidated_without_the_internal_one(rig: Rig) -> None:
    panel = rig.panel
    bot_b = await make_bot(panel, panel.tenant_b, panel.admin_b)
    await run(panel.admin_a, rig.bot["id"])
    await run(panel.admin_b, bot_b["id"])

    await panel.staff.put("/auth/context", json={"client_id": None}, headers=csrf(panel.staff))
    data = (await panel.staff.get("/dashboard?period=30d")).json()
    assert data["all_clients"] is True and data["period"] == "30d"
    assert data["clients_active"] >= 2
    names = {c["client_name"] for c in data["by_client"]}
    assert {"Tenant A", "Tenant B"} <= names
    assert "Artemisys" not in names, "the hidden internal client never shows"
    assert data["machines"] == [] and data["pending_runs"] == []
    row_a = next(c for c in data["by_client"] if c["client_name"] == "Tenant A")
    assert row_a["runs"] >= 1 and row_a["machines_total"] >= 1


async def test_period_is_validated_and_a_user_must_be_logged_in(rig: Rig) -> None:
    assert (await rig.panel.admin_a.get("/dashboard?period=forever")).status_code == 422
    assert (await rig.panel.admin_a.get("/dashboard?period=today")).status_code == 200
    assert (await rig.agent.client.get("/dashboard")).status_code == 401
