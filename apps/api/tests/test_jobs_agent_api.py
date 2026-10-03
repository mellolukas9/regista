"""How the agent gets and ends a run: atomic take, one run per machine, long-polling, wake-ups
(M3, docs/adr/0020)."""

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_api.jobs.waiters import JobListener, JobWaiters

from .agent_helpers import AgentSim
from .conftest import DbUrls
from .helpers import Panel, new_client
from .jobs_helpers import Rig, _post, enrolled_agent, make_bot, run, set_job

Factory = async_sessionmaker[AsyncSession]


async def _second_agent(rig: Rig) -> AgentSim:
    return await rig.second_agent()


# --- taking a run -----------------------------------------------------------------------------


async def test_the_agent_takes_the_oldest_pending_run_of_its_pool(rig: Rig) -> None:
    empty = await rig.agent.next_job(wait=0)
    assert empty.status_code == 204 and empty.content == b""

    first = await run(rig.panel.admin_a, rig.bot["id"])
    second = await run(rig.panel.admin_a, rig.bot["id"])
    got = await rig.agent.next_job()
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["job_id"] == first["id"] and body["short_code"] == first["short_code"]
    assert body["package_name"] == rig.bot["package_name"]
    assert body["params"] == {} and body["timeout_seconds"] > 0

    shown = (await rig.panel.admin_a.get(f"/jobs/{first['id']}")).json()
    assert shown["status"] == "assigned" and shown["machine_id"] == rig.agent.machine_id
    assert shown["assigned_at"] is not None
    # One run per machine at a time: the second waits, even though it is pending and eligible.
    assert (await rig.agent.next_job()).status_code == 204
    assert (await rig.panel.admin_a.get(f"/jobs/{second['id']}")).json()["status"] == "pending"


async def test_a_machine_that_finished_gets_the_next_run(rig: Rig) -> None:
    first = await run(rig.panel.admin_a, rig.bot["id"])
    second = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).json()["job_id"] == first["id"]
    assert (await rig.agent.job_call(first["id"], "start")).status_code == 200
    assert (await rig.agent.job_call(first["id"], "complete")).status_code == 200
    assert (await rig.agent.next_job()).json()["job_id"] == second["id"]


async def test_two_machines_never_take_the_same_run(rig: Rig) -> None:
    other = await _second_agent(rig)
    jobs = [await run(rig.panel.admin_a, rig.bot["id"]) for _ in range(2)]
    results = await asyncio.gather(rig.agent.next_job(), other.next_job())
    assert [r.status_code for r in results] == [200, 200]
    taken = {r.json()["job_id"] for r in results}
    assert taken == {j["id"] for j in jobs}, "each machine got a different run"

    # One run, two machines asking at once: exactly one gets it.
    for agent, r in zip((rig.agent, other), results, strict=True):
        await agent.job_call(r.json()["job_id"], "start")
        await agent.job_call(r.json()["job_id"], "complete")
    job = await run(rig.panel.admin_a, rig.bot["id"])
    both = await asyncio.gather(rig.agent.next_job(), other.next_job())
    assert sorted(r.status_code for r in both) == [200, 204]
    winner = next(r for r in both if r.status_code == 200)
    assert winner.json()["job_id"] == job["id"]


async def test_the_same_machine_asking_twice_at_once_gets_one_run(rig: Rig) -> None:
    for _ in range(2):
        await run(rig.panel.admin_a, rig.bot["id"])
    both = await asyncio.gather(*(rig.agent.next_job() for _ in range(4)))
    assert sorted(r.status_code for r in both) == [200, 204, 204, 204]
    active = await _count(
        rig.panel,
        "SELECT count(*) FROM jobs WHERE machine_id = :m AND status IN ('assigned', 'running')",
        {"m": rig.agent.machine_id},
    )
    assert active == 1


async def test_only_the_pool_of_the_machine_and_only_its_client(rig: Rig) -> None:
    # Another pool of the same client.
    other_bot = await make_bot(rig.panel, rig.panel.tenant_a, rig.panel.admin_a)
    job = await run(rig.panel.admin_a, other_bot["id"])
    assert (await rig.agent.next_job()).status_code == 204

    # Client B has a machine in a pool of the same name: still not the same pool.
    bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
    agent_b = await enrolled_agent(
        rig.panel, rig.panel.tenant_b, rig.panel.admin_b, bot_b["pool_id"], rig.agent_client
    )
    assert (await agent_b.next_job()).status_code == 204
    assert (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "pending"

    # And the machine that does belong gets it.
    owner = await enrolled_agent(
        rig.panel, rig.panel.tenant_a, rig.panel.admin_a, other_bot["pool_id"], rig.agent_client
    )
    assert (await owner.next_job()).json()["job_id"] == job["id"]


async def test_a_machine_without_signal_or_revoked_takes_nothing(rig: Rig) -> None:
    await run(rig.panel.admin_a, rig.bot["id"])
    await _sql(rig.panel, "UPDATE machines SET status = 'offline' WHERE id = :m", rig.agent)
    assert (await rig.agent.next_job()).status_code == 204
    await _sql(rig.panel, "UPDATE machines SET status = 'online' WHERE id = :m", rig.agent)
    assert (await rig.agent.next_job()).status_code == 200

    revoke = await _post(
        rig.panel.admin_a,
        f"/machines/{rig.agent.machine_id}/revoke",
        {"confirm_name": await _name(rig)},
    )
    assert revoke.status_code == 200, revoke.text
    assert (await rig.agent.next_job()).status_code == 401


async def test_cancelled_requests_are_not_handed_out(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await set_job(rig.panel, rig.panel.tenant_a, job["id"], cancel_requested_at=datetime.now(UTC))
    assert (await rig.agent.next_job()).status_code == 204


# --- long-polling -----------------------------------------------------------------------------


async def test_a_waiting_agent_is_woken_by_a_new_run(rig: Rig) -> None:
    started = time.monotonic()
    waiting = asyncio.create_task(rig.agent.next_job(wait=20))
    await asyncio.sleep(0.5)
    assert rig.panel.env.app.state.job_waiters.count == 1
    job = await run(rig.panel.admin_a, rig.bot["id"])
    got = await asyncio.wait_for(waiting, timeout=8)
    assert got.status_code == 200 and got.json()["job_id"] == job["id"]
    assert time.monotonic() - started < 6, "it was woken, it did not wait for the safety tick"
    assert rig.panel.env.app.state.job_waiters.count == 0


async def test_a_run_of_another_pool_does_not_wake_an_agent_into_a_job(rig: Rig) -> None:
    other_bot = await make_bot(rig.panel, rig.panel.tenant_a, rig.panel.admin_a)
    waiting = asyncio.create_task(rig.agent.next_job(wait=2))
    await asyncio.sleep(0.3)
    await run(rig.panel.admin_a, other_bot["id"])
    assert (await waiting).status_code == 204


async def test_the_wait_ends_with_204_after_the_servers_limit(panel: Panel) -> None:
    panel.env.app.state.settings = panel.env.app.state.settings.model_copy(
        update={"agent_poll_wait_seconds": 1}
    )
    agent_client = new_client(panel.env.app)
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    agent = await enrolled_agent(panel, panel.tenant_a, panel.admin_a, bot["pool_id"], agent_client)
    started = time.monotonic()
    r = await agent.next_job(wait=30)
    assert r.status_code == 204
    assert 0.8 < time.monotonic() - started < 5
    await agent_client.aclose()


async def test_waiting_agents_hold_no_database_connection(rig: Rig, db_urls: object) -> None:
    """Fifty agents waiting at once cost fifty coroutines, not fifty pooled connections."""
    waiters: JobWaiters = rig.panel.env.app.state.job_waiters
    pool = rig.panel.env.app.state.session_factory.kw["bind"].pool
    tasks = [asyncio.create_task(rig.agent.next_job(wait=3)) for _ in range(50)]
    await asyncio.sleep(1.0)
    assert waiters.count == 50
    assert pool.checkedout() <= 3
    results = await asyncio.gather(*tasks)
    assert all(r.status_code == 204 for r in results)
    assert waiters.count == 0


async def test_too_many_waiters_get_503_with_retry_after(rig: Rig) -> None:
    app = rig.panel.env.app
    original = app.state.job_waiters
    app.state.job_waiters = JobWaiters(max_waiters=2)
    try:
        tasks = [asyncio.create_task(rig.agent.next_job(wait=2)) for _ in range(2)]
        await asyncio.sleep(0.5)
        r = await rig.agent.next_job(wait=1)
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "too_many_waiters"
        assert r.headers["retry-after"] == "5"
        await asyncio.gather(*tasks)
    finally:
        app.state.job_waiters = original


# --- the listening connection -----------------------------------------------------------------


async def _terminate(db_urls: DbUrls, pid: int) -> None:
    """Kill a backend as the Postgres superuser (the app and owner roles may not)."""
    dsn = re.sub(r"//[^@]+@", "//postgres:test-superuser@", db_urls.app).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_terminate_backend($1)", pid)
    finally:
        await conn.close()


async def _kill_listener(rig: Rig, db_urls: DbUrls) -> None:
    listener: JobListener = rig.panel.env.app.state.job_listener
    await asyncio.wait_for(listener.connected.wait(), timeout=5)
    conn = listener._connection
    assert conn is not None
    await _terminate(db_urls, conn.get_server_pid())
    # It notices (the old connection is gone), reconnects with a new one and says so.
    deadline = time.monotonic() + 15
    while listener._connection is conn:
        assert time.monotonic() < deadline, "the listener did not notice the drop"
        await asyncio.sleep(0.05)
    while not (listener.connected.is_set() and listener._connection is not None):
        assert time.monotonic() < deadline, "the listener did not come back"
        await asyncio.sleep(0.05)


async def test_the_listener_reconnects_and_wakes_up_agents_that_missed_a_notification(
    rig: Rig, db_urls: DbUrls
) -> None:
    waiting = asyncio.create_task(rig.agent.next_job(wait=25))
    await asyncio.sleep(0.5)
    listener: JobListener = rig.panel.env.app.state.job_listener
    await asyncio.wait_for(listener.connected.wait(), timeout=5)
    assert listener._connection is not None
    await _terminate(db_urls, listener._connection.get_server_pid())
    # Created while the listener is (probably) down: the NOTIFY may be lost, the run is not.
    job = await run(rig.panel.admin_a, rig.bot["id"])
    got = await asyncio.wait_for(waiting, timeout=20)
    assert got.status_code == 200 and got.json()["job_id"] == job["id"]
    await asyncio.wait_for(listener.connected.wait(), timeout=15)


async def test_after_a_reconnect_notifications_wake_agents_again(rig: Rig, db_urls: DbUrls) -> None:
    await _kill_listener(rig, db_urls)
    started = time.monotonic()
    waiting = asyncio.create_task(rig.agent.next_job(wait=25))
    await asyncio.sleep(0.5)
    job = await run(rig.panel.admin_a, rig.bot["id"])
    got = await asyncio.wait_for(waiting, timeout=8)
    assert got.json()["job_id"] == job["id"]
    assert time.monotonic() - started < 6


# --- start, complete, fail --------------------------------------------------------------------


async def _taken(rig: Rig) -> str:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).json()["job_id"] == job["id"]
    return str(job["id"])


async def test_start_then_complete(rig: Rig) -> None:
    job_id = await _taken(rig)
    started = await rig.agent.job_call(job_id, "start")
    assert started.status_code == 200
    assert started.json() == {"status": "running", "cancel_requested": False}
    again = await rig.agent.job_call(job_id, "start")
    assert again.status_code == 409 and again.json()["detail"]["status"] == "running"

    done = await rig.agent.job_call(job_id, "complete")
    assert done.status_code == 200 and done.json()["status"] == "completed"
    shown = (await rig.panel.admin_a.get(f"/jobs/{job_id}")).json()
    assert shown["status"] == "completed"
    assert shown["started_at"] is not None and shown["finished_at"] is not None
    # Finished is final.
    assert (await rig.agent.job_call(job_id, "complete")).status_code == 409
    assert (
        await rig.agent.job_call(job_id, "fail", {"error_code": "robot_failed"})
    ).status_code == 409


async def test_complete_needs_a_started_run(rig: Rig) -> None:
    job_id = await _taken(rig)
    r = await rig.agent.job_call(job_id, "complete")
    assert r.status_code == 409 and r.json()["detail"]["status"] == "assigned"


async def test_fail_records_the_code_and_a_scrubbed_message(rig: Rig) -> None:
    job_id = await _taken(rig)
    await rig.agent.job_call(job_id, "start")
    secret = "rga1.AAAA.BBBB token=hunter2 Bearer abc123"
    r = await rig.agent.job_call(
        job_id, "fail", {"error_code": "robot_failed", "message": f"\x1b[31mboom\x1b[0m {secret}"}
    )
    assert r.status_code == 200 and r.json()["status"] == "failed"
    shown = (await rig.panel.admin_a.get(f"/jobs/{job_id}")).json()
    assert shown["status"] == "failed" and shown["error_code"] == "robot_failed"
    for leaked in ("rga1.AAAA", "hunter2", "abc123", "\x1b"):
        assert leaked not in shown["error_message"]
    assert "boom" in shown["error_message"]


async def test_the_agent_cannot_make_up_server_side_codes_or_unrequested_cancellations(
    rig: Rig,
) -> None:
    job_id = await _taken(rig)
    await rig.agent.job_call(job_id, "start")
    for code in ("machine_lost", "machine_revoked", "nope"):
        r = await rig.agent.job_call(job_id, "fail", {"error_code": code})
        assert r.status_code == 422, code
    r = await rig.agent.job_call(job_id, "fail", {"error_code": "cancelled"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cancel_not_requested"


async def test_a_requested_cancellation_ends_as_cancelled(rig: Rig) -> None:
    job_id = await _taken(rig)
    started = await rig.agent.job_call(job_id, "start")
    assert started.json()["cancel_requested"] is False
    asked = await _post(rig.panel.admin_a, f"/jobs/{job_id}/cancel")
    assert asked.status_code == 200 and asked.json()["status"] == "running"
    r = await rig.agent.job_call(job_id, "fail", {"error_code": "cancelled"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    shown = (await rig.panel.admin_a.get(f"/jobs/{job_id}")).json()
    assert shown["status"] == "cancelled" and shown["error_code"] is None


async def test_a_machine_only_touches_its_own_runs(rig: Rig) -> None:
    job_id = await _taken(rig)
    other = await _second_agent(rig)
    for action in ("start", "complete"):
        assert (await other.job_call(job_id, action)).status_code == 404
    r = await other.job_call(job_id, "fail", {"error_code": "internal"})
    assert r.status_code == 404
    assert (await other.job_call(str(uuid.uuid4()), "start")).status_code == 404
    shown = (await rig.panel.admin_a.get(f"/jobs/{job_id}")).json()
    assert shown["status"] == "assigned" and shown["machine_id"] == rig.agent.machine_id


# --- helpers ----------------------------------------------------------------------------------


async def _sql(panel: Panel, statement: str, agent: AgentSim) -> None:
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=panel.tenant_a) as db:
        await db.execute(text(statement), {"m": agent.machine_id})


async def _count(panel: Panel, statement: str, params: dict[str, str]) -> int:
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=panel.tenant_a) as db:
        value: int = (await db.execute(text(statement), params)).scalar_one()
    return value


async def _name(rig: Rig) -> str:
    async with tenant_session(
        rig.panel.env.app.state.session_factory, tenant_id=rig.panel.tenant_a
    ) as db:
        name: str = (
            await db.execute(
                text("SELECT name FROM machines WHERE id = :m"), {"m": rig.agent.machine_id}
            )
        ).scalar_one()
    return name
