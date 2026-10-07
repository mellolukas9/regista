"""Cancellation by heartbeat, revoking a machine with a run, runs of lost machines (M3)."""

import uuid
from datetime import UTC, datetime

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_api.jobs import lost
from regista_api.main import create_app

from .conftest import DbUrls, api_client, make_settings
from .helpers import csrf
from .jobs_helpers import Rig, _post, enrolled_agent, make_bot, run

Factory = async_sessionmaker[AsyncSession]


async def _take_and_start(rig: Rig) -> str:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    got = await rig.agent.next_job()
    assert got.json()["job_id"] == job["id"]
    assert (await rig.agent.job_call(job["id"], "start")).status_code == 200
    return str(job["id"])


async def _offline(rig: Rig) -> None:
    async with tenant_session(
        rig.panel.env.app.state.session_factory, tenant_id=rig.panel.tenant_a
    ) as db:
        await db.execute(
            text("UPDATE machines SET status = 'offline' WHERE id = :m"),
            {"m": rig.agent.machine_id},
        )


async def _job(rig: Rig, job_id: str) -> dict[str, object]:
    body: dict[str, object] = (await rig.panel.admin_a.get(f"/jobs/{job_id}")).json()
    return body


# --- cancellation reaches the agent through the heartbeat ---------------------------------------


async def test_the_heartbeat_carries_cancellations_of_this_machine(rig: Rig) -> None:
    job_id = await _take_and_start(rig)
    quiet = await rig.agent.heartbeat(current_job_id=job_id)
    assert quiet.status_code == 200 and quiet.json()["cancellations"] == []

    assert (await _post(rig.panel.operator_a, f"/jobs/{job_id}/cancel")).status_code == 200
    asked = await rig.agent.heartbeat(current_job_id=job_id)
    assert asked.json()["cancellations"] == [job_id]
    # Even if the agent does not say what it runs: the server knows.
    assert (await rig.agent.heartbeat()).json()["cancellations"] == [job_id]


async def test_a_run_assigned_but_not_started_is_cancelled_the_same_way(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).status_code == 200
    await _post(rig.panel.admin_a, f"/jobs/{job['id']}/cancel")
    assert (await rig.agent.heartbeat()).json()["cancellations"] == [job["id"]]
    started = await rig.agent.job_call(job["id"], "start")
    assert started.json()["cancel_requested"] is True
    done = await rig.agent.job_call(job["id"], "fail", {"error_code": "cancelled"})
    assert done.json()["status"] == "cancelled"
    assert (await rig.agent.heartbeat()).json()["cancellations"] == []


async def test_an_agent_is_told_to_stop_a_run_the_server_already_ended(rig: Rig) -> None:
    job_id = await _take_and_start(rig)
    await _offline(rig)
    assert await lost.end_runs_of_lost_machines(rig.panel.env.app.state.session_factory) == 1
    # The machine comes back (a heartbeat) still running the robot: stop it.
    r = await rig.agent.heartbeat(current_job_id=job_id)
    assert r.json()["cancellations"] == [job_id]


async def test_a_heartbeat_never_names_a_run_of_another_machine(rig: Rig) -> None:
    bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
    job_b = await run(rig.panel.admin_b, bot_b["id"])
    await _post(rig.panel.admin_b, f"/jobs/{job_b['id']}/cancel")
    r = await rig.agent.heartbeat(current_job_id=job_b["id"])
    assert r.status_code == 200 and r.json()["cancellations"] == []
    r = await rig.agent.heartbeat(current_job_id=str(uuid.uuid4()))
    assert r.json()["cancellations"] == []


# --- revoking a machine -----------------------------------------------------------------------


async def test_the_panel_shows_what_a_machine_is_doing(rig: Rig) -> None:
    job_id = await _take_and_start(rig)
    detail = (await rig.panel.admin_a.get(f"/machines/{rig.agent.machine_id}")).json()
    assert detail["current_job"]["id"] == job_id
    assert detail["current_job"]["status"] == "running"
    listed = (await rig.panel.admin_a.get(f"/machines?pool_id={rig.bot['pool_id']}")).json()
    assert listed["items"][0]["current_job"]["short_code"].startswith("exec-")
    pools = (await rig.panel.admin_a.get("/pools")).json()["items"]
    mine = next(p for p in pools if p["id"] == rig.bot["pool_id"])
    assert mine["bot_names"] == [rig.bot["name"]]


async def test_revoking_a_machine_cancels_its_run_and_the_agent_stops(rig: Rig) -> None:
    job_id = await _take_and_start(rig)
    name = (await rig.panel.admin_a.get(f"/machines/{rig.agent.machine_id}")).json()["name"]
    r = await _post(
        rig.panel.admin_a, f"/machines/{rig.agent.machine_id}/revoke", {"confirm_name": name}
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_job"] is None
    shown = await _job(rig, job_id)
    assert shown["status"] == "cancelled" and shown["error_code"] == "machine_revoked"
    assert shown["finished_at"] is not None
    # The agent finds out because its token is refused, and cannot report anything any more.
    assert (await rig.agent.heartbeat()).status_code == 401
    assert (await rig.agent.job_call(job_id, "complete")).status_code == 401


async def test_revoking_an_idle_machine_leaves_other_runs_alone(rig: Rig) -> None:
    other = await rig.second_agent()
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await other.next_job()).json()["job_id"] == job["id"]
    name = (await rig.panel.admin_a.get(f"/machines/{rig.agent.machine_id}")).json()["name"]
    await _post(
        rig.panel.admin_a, f"/machines/{rig.agent.machine_id}/revoke", {"confirm_name": name}
    )
    assert (await _job(rig, job["id"]))["status"] == "assigned"


# --- runs of lost machines --------------------------------------------------------------------


async def test_a_silent_machine_ends_its_run_as_machine_lost(rig: Rig) -> None:
    factory = rig.panel.env.app.state.session_factory
    assigned = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).status_code == 200
    # Online machine: nothing happens.
    assert await lost.end_runs_of_lost_machines(factory) == 0
    await _offline(rig)
    assert await lost.end_runs_of_lost_machines(factory) == 1
    shown = await _job(rig, assigned["id"])
    assert shown["status"] == "failed" and shown["error_code"] == "machine_lost"
    assert shown["finished_at"] is not None
    # Idempotent.
    assert await lost.end_runs_of_lost_machines(factory) == 0


async def test_a_running_run_is_lost_too_and_a_requested_cancellation_wins(rig: Rig) -> None:
    factory = rig.panel.env.app.state.session_factory
    first = await _take_and_start(rig)
    await _post(rig.panel.admin_a, f"/jobs/{first}/cancel")
    await _offline(rig)
    assert await lost.end_runs_of_lost_machines(factory) == 1
    shown = await _job(rig, first)
    assert shown["status"] == "cancelled" and shown["error_code"] is None


async def test_a_machine_back_online_keeps_its_run(rig: Rig) -> None:
    factory = rig.panel.env.app.state.session_factory
    job_id = await _take_and_start(rig)
    await _offline(rig)
    # The heartbeat arrives before the sweep writes.
    assert (await rig.agent.heartbeat(current_job_id=job_id)).status_code == 200
    assert await lost.end_runs_of_lost_machines(factory) == 0
    assert (await _job(rig, job_id))["status"] == "running"


async def test_the_lost_machine_task_leaves_other_clients_alone(rig: Rig) -> None:
    """Client A loses a machine; client B has a run in progress on an online machine and another
    on an offline machine of its own. Only A's run and B's own offline one end, each in its own
    client, and nothing else of B changes."""
    panel = rig.panel
    factory = panel.env.app.state.session_factory
    bot_b = await make_bot(panel, panel.tenant_b, panel.admin_b)
    agent_b = await enrolled_agent(
        panel, panel.tenant_b, panel.admin_b, bot_b["pool_id"], rig.agent_client
    )
    job_b = await run(panel.admin_b, bot_b["id"])
    assert (await agent_b.next_job()).json()["job_id"] == job_b["id"]
    await agent_b.job_call(job_b["id"], "start")
    job_a = await _take_and_start(rig)

    async def snapshot() -> list[str]:
        async with tenant_session(factory, tenant_id=panel.tenant_b) as db:
            rows = await db.execute(
                text(
                    "SELECT row_to_json(j)::text FROM jobs j"
                    " UNION ALL SELECT row_to_json(m)::text FROM machines m ORDER BY 1"
                )
            )
            return [r[0] for r in rows]

    before = await snapshot()
    await _offline(rig)  # only A's machine
    assert await lost.end_runs_of_lost_machines(factory) == 1
    assert await snapshot() == before
    assert (await _job(rig, job_a))["error_code"] == "machine_lost"
    assert (await panel.admin_b.get(f"/jobs/{job_b['id']}")).json()["status"] == "running"


async def test_the_lost_run_is_audited_as_the_system(rig: Rig, owner_factory: Factory) -> None:
    job_id = await _take_and_start(rig)
    await _offline(rig)
    await lost.end_runs_of_lost_machines(rig.panel.env.app.state.session_factory)
    async with tenant_session(owner_factory, tenant_id=rig.panel.tenant_a) as db:
        row = (
            await db.execute(
                text(
                    "SELECT actor_type, action, metadata->>'because' AS why FROM audit_log"
                    " WHERE target_id = :j AND action = 'job.failed'"
                ),
                {"j": job_id},
            )
        ).one()
    assert (row.actor_type, row.why) == ("system", "machine_lost")


# --- partitions of the logs -------------------------------------------------------------------


async def test_the_worker_creates_the_partitions_and_it_is_idempotent(rig: Rig) -> None:
    factory = rig.panel.env.app.state.session_factory
    assert await lost.ensure_log_partitions(factory) == 0  # the migration made them
    assert await lost.ensure_log_partitions(factory, months_ahead=6) >= 0
    assert await lost.ensure_log_partitions(factory, months_ahead=6) == 0


async def test_health_says_degraded_when_the_next_partition_is_missing(
    empty_db_urls: DbUrls,
) -> None:
    dsn = empty_db_urls.owner.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    now = datetime.now(UTC)
    nxt = (now.year + (now.month == 12), now.month % 12 + 1)
    current_name = f"job_logs_{now.year}{now.month:02d}"
    next_name = f"job_logs_{nxt[0]}{nxt[1]:02d}"
    try:
        async with api_client(create_app(make_settings(empty_db_urls))) as client:
            assert (await client.get("/health")).json() == {"status": "ok"}

            await conn.execute(f"DROP TABLE {next_name}")
            r = await client.get("/health")
            assert r.status_code == 200  # a degraded API still serves
            assert r.json() == {"status": "degraded", "reason": "job_logs_partition_missing"}

            await conn.execute("SELECT app.ensure_job_log_partitions(3)")
            assert (await client.get("/health")).json() == {"status": "ok"}

            await conn.execute(f"DROP TABLE {current_name}")
            assert (await client.get("/health")).json()["status"] == "degraded"
            await conn.execute("SELECT app.ensure_job_log_partitions(3)")
    finally:
        await conn.close()


async def test_csrf_is_still_required_on_cancel(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    r = await rig.panel.admin_a.post(f"/jobs/{job['id']}/cancel")  # no CSRF header
    assert r.status_code == 403
    assert csrf(rig.panel.admin_a)["X-CSRF-Token"]
    assert (await _job(rig, job["id"]))["status"] == "pending"
