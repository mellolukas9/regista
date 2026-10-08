"""The agent side of signed packages (M4, ADR 0021): the version a run carries, the package a
machine may fetch, the runtimes it must prepare, and the reasons it may report for a refusal."""

import hashlib
import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from regista_api.core.db import tenant_session
from regista_pkg import parse_signature_doc, verify

from .helpers import csrf, new_client
from .jobs_helpers import Rig, enrolled_agent, make_bot, run
from .package_helpers import SigningKey
from .test_versions_api import _publish

pytestmark = pytest.mark.s3


async def _use(rig: Rig, version_id: str) -> None:
    staff = await rig.panel.staff_in(rig.panel.tenant_a)
    r = await staff.put(
        f"/bots/{rig.bot['id']}/current-version",
        json={"version_id": version_id},
        headers=csrf(staff),
    )
    assert r.status_code == 200, r.text


async def _take(rig: Rig) -> dict[str, Any]:
    r = await rig.agent.next_job()
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


# --- the version a run carries ----------------------------------------------------------------


async def test_a_run_carries_the_version_in_use_when_a_machine_takes_it(
    rig: Rig, signing: SigningKey
) -> None:
    one = await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert job["id"]
    taken = await _take(rig)
    assert taken["bot_version_id"] == one["id"] and taken["version"] == "1.0.0"
    detail = (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()
    assert detail["id"] == job["id"] and detail["bot_version"] == "1.0.0"


async def test_switching_the_version_changes_the_next_run_and_the_pending_ones(
    rig: Rig, signing: SigningKey
) -> None:
    one = await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    two = await _publish(rig.panel, signing, rig.bot, version="1.1.0")
    first = await run(rig.panel.admin_a, rig.bot["id"])
    taken = await _take(rig)
    assert taken["job_id"] == first["id"] and taken["bot_version_id"] == one["id"]
    await rig.agent.job_call(first["id"], "start")
    await rig.agent.job_call(first["id"], "complete")

    waiting = await run(rig.panel.admin_a, rig.bot["id"])  # created while 1.0.0 is in use...
    await _use(rig, two["id"])  # ...and the version is switched before a machine takes it
    again = await _take(rig)
    assert again["job_id"] == waiting["id"]
    assert again["bot_version_id"] == two["id"] and again["version"] == "1.1.0"


async def test_a_run_of_a_bot_without_a_version_has_none_outside_production(rig: Rig) -> None:
    await run(rig.panel.admin_a, rig.bot["id"])
    taken = await _take(rig)
    assert taken["bot_version_id"] is None and taken["version"] is None


# --- production: nothing runs unsigned --------------------------------------------------------


async def test_production_refuses_a_bot_without_a_version_in_use(
    rig: Rig, signing: SigningKey
) -> None:
    app = rig.panel.env.app
    original = app.state.settings
    app.state.settings = original.model_copy(update={"environment": "prod"})
    try:
        r = await rig.panel.admin_a.post(
            "/jobs", json={"bot_id": rig.bot["id"]}, headers=csrf(rig.panel.admin_a)
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "bot_has_no_version"
    finally:
        app.state.settings = original

    await _publish(rig.panel, signing, rig.bot, version="1.0.0")  # published, but not in use
    app.state.settings = original.model_copy(update={"environment": "prod"})
    try:
        r = await rig.panel.admin_a.post(
            "/jobs", json={"bot_id": rig.bot["id"]}, headers=csrf(rig.panel.admin_a)
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "bot_has_no_version_in_use"
    finally:
        app.state.settings = original


async def test_production_accepts_a_bot_with_a_version_in_use(
    rig: Rig, signing: SigningKey
) -> None:
    await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    app = rig.panel.env.app
    original = app.state.settings
    app.state.settings = original.model_copy(update={"environment": "prod"})
    try:
        r = await rig.panel.admin_a.post(
            "/jobs", json={"bot_id": rig.bot["id"]}, headers=csrf(rig.panel.admin_a)
        )
        assert r.status_code == 201, r.text
    finally:
        app.state.settings = original


async def test_in_production_a_machine_never_takes_a_run_of_a_bot_without_a_version(
    rig: Rig,
) -> None:
    """A run that got in some other way (created before, say) waits instead of running unsigned."""
    job = await run(rig.panel.admin_a, rig.bot["id"])
    app = rig.panel.env.app
    original = app.state.settings
    app.state.settings = original.model_copy(update={"environment": "prod"})
    try:
        assert (await rig.agent.next_job()).status_code == 204
    finally:
        app.state.settings = original
    assert (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "pending"


# --- the package ------------------------------------------------------------------------------


async def test_a_machine_fetches_the_package_of_its_run_and_can_verify_it(
    rig: Rig, signing: SigningKey
) -> None:
    version = await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)

    r = await rig.agent.package(version["id"])
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["version"] == "1.0.0" and info["package_name"] == rig.bot["package_name"]
    assert info["expires_in"] == 120 and "X-Amz-Signature=" in info["download_url"]
    assert "tenants/" not in r.text.replace(info["download_url"], "")

    # The agent verifies with its own keys, from what the server handed over.
    signed, signature = parse_signature_doc(info["signature_doc"].encode())
    verify(signed, signature, rig.panel.env.app.state.settings.package_trusted_keys())
    assert signed.sha256 == info["sha256"] and signed.size == info["size_bytes"]
    async with httpx.AsyncClient() as plain:  # the URL is the credential
        data = (await plain.get(info["download_url"])).content
    assert hashlib.sha256(data).hexdigest() == info["sha256"] and len(data) == info["size_bytes"]


async def test_a_machine_without_a_run_of_that_version_does_not_get_the_package(
    rig: Rig, signing: SigningKey
) -> None:
    version = await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    assert (await rig.agent.package(version["id"])).status_code == 404  # no run yet
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    assert (await rig.agent.package(version["id"])).status_code == 200
    await rig.agent.job_call(job["id"], "start")
    await rig.agent.job_call(job["id"], "complete")
    assert (await rig.agent.package(version["id"])).status_code == 404  # the run is over


async def test_another_machine_of_the_same_client_does_not_get_it_either(
    rig: Rig, signing: SigningKey
) -> None:
    version = await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    other = await rig.second_agent()
    assert (await other.package(version["id"])).status_code == 404


async def test_a_machine_of_another_client_does_not_get_it(rig: Rig, signing: SigningKey) -> None:
    version = await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    b_client = new_client(rig.panel.env.app)
    try:
        bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
        agent_b = await enrolled_agent(
            rig.panel, rig.panel.tenant_b, rig.panel.admin_b, bot_b["pool_id"], b_client
        )
        assert (await agent_b.package(version["id"])).status_code == 404
        assert (await agent_b.package(str(uuid.uuid4()))).status_code == 404
    finally:
        await b_client.aclose()


async def test_an_expired_or_unknown_version_is_not_found(rig: Rig) -> None:
    assert (await rig.agent.package(str(uuid.uuid4()))).status_code == 404


# --- runtimes ---------------------------------------------------------------------------------


async def test_runtimes_are_those_of_the_versions_in_use_in_the_machines_pool(
    rig: Rig, signing: SigningKey
) -> None:
    assert (await rig.agent.runtimes()).json() == {"runtimes": []}
    await _publish(rig.panel, signing, rig.bot, version="1.0.0")  # published, not in use
    assert (await rig.agent.runtimes()).json() == {"runtimes": []}
    await _publish(rig.panel, signing, rig.bot, version="1.1.0", activate=True)
    runtimes = (await rig.agent.runtimes()).json()["runtimes"]
    assert runtimes == [
        {
            "package_name": rig.bot["package_name"],
            "version": "1.1.0",
            "python": "3.13.5",
            "playwright": None,
            "chromium_revision": None,
        }
    ]


async def test_a_machine_does_not_learn_the_runtimes_of_another_clients_bots(
    rig: Rig, signing: SigningKey
) -> None:
    b_client = new_client(rig.panel.env.app)
    try:
        bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
        agent_b = await enrolled_agent(
            rig.panel, rig.panel.tenant_b, rig.panel.admin_b, bot_b["pool_id"], b_client
        )
        await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
        assert (await agent_b.runtimes()).json() == {"runtimes": []}
    finally:
        await b_client.aclose()


# --- the reasons of a refusal -----------------------------------------------------------------


async def _fail(rig: Rig, **body: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    await rig.agent.job_call(job["id"], "start")
    r = await rig.agent.job_call(job["id"], "fail", body)
    assert r.status_code == 200, r.text
    detail: dict[str, Any] = (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()
    return detail, job


@pytest.mark.parametrize("reason", ["hash_mismatch", "wrong_client", "unsafe_archive"])
async def test_a_package_refusal_keeps_its_reason_but_not_the_free_text(
    rig: Rig, reason: str
) -> None:
    detail, _ = await _fail(
        rig,
        error_code="package_invalid",
        reason=reason,
        message="texto livre <script>alert(1)</script> do agente",
    )
    assert detail["status"] == "failed" and detail["error_code"] == "package_invalid"
    assert detail["error_reason"] == reason
    assert "script" not in json.dumps(detail) and not detail.get("error_message")


async def test_a_reason_outside_the_closed_list_is_dropped(rig: Rig) -> None:
    detail, _ = await _fail(
        rig, error_code="package_invalid", reason="the-agent-says-so", message="x"
    )
    assert detail["error_code"] == "package_invalid" and detail["error_reason"] is None


@pytest.mark.parametrize(
    "code",
    ["robot_not_allowed", "runtime_missing", "environment_failed", "robot_host_unavailable"],
)
async def test_the_other_new_codes_keep_no_free_text(rig: Rig, code: str) -> None:
    detail, _ = await _fail(rig, error_code=code, reason="hash_mismatch", message="segredo?")
    assert detail["error_code"] == code and detail["error_reason"] is None
    assert not detail.get("error_message") and "segredo" not in json.dumps(detail)


async def test_a_reason_means_nothing_for_another_code(rig: Rig) -> None:
    detail, _ = await _fail(rig, error_code="robot_failed", reason="hash_mismatch", message="bug")
    assert detail["error_code"] == "robot_failed" and detail["error_reason"] is None
    assert detail["error_message"] == "bug"


async def test_an_unknown_error_code_is_refused(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    await rig.agent.job_call(job["id"], "start")
    r = await rig.agent.job_call(job["id"], "fail", {"error_code": "because_i_said_so"})
    assert r.status_code == 422


# --- the kill switch indication ---------------------------------------------------------------


async def test_the_heartbeat_tells_the_panel_the_machine_is_paused_locally(rig: Rig) -> None:
    machines = rig.panel.admin_a
    machine_id = rig.agent.machine_id
    detail = (await machines.get(f"/machines/{machine_id}")).json()
    assert detail["paused_locally"] is False

    assert (await rig.agent.heartbeat(paused=True)).status_code == 200
    detail = (await machines.get(f"/machines/{machine_id}")).json()
    assert detail["paused_locally"] is True
    listed = (await machines.get("/machines", params={"pool_id": detail["pool_id"]})).json()[
        "items"
    ]
    assert any(m["id"] == machine_id and m["paused_locally"] for m in listed)

    assert (await rig.agent.heartbeat(paused=False)).status_code == 200
    assert (await machines.get(f"/machines/{machine_id}")).json()["paused_locally"] is False


async def test_no_route_of_the_panel_pauses_or_resumes_a_machine(rig: Rig) -> None:
    app = rig.panel.env.app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if "pause" in p or "resume" in p]


# --- giving a run back (the kill switch went on while the agent waited) ----------------------


async def test_a_machine_can_give_back_a_run_it_has_not_started(
    rig: Rig, signing: SigningKey
) -> None:
    await _publish(rig.panel, signing, rig.bot, version="1.0.0", activate=True)
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    assert (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "assigned"

    r = await rig.agent.job_call(job["id"], "release")
    assert r.status_code == 200 and r.json()["status"] == "pending"
    detail = (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()
    assert detail["status"] == "pending" and detail["machine_id"] is None
    assert detail["assigned_at"] is None and detail["bot_version"] is None

    other = await rig.second_agent()  # another machine of the pool takes it
    taken = (await other.next_job()).json()
    assert taken["job_id"] == job["id"] and taken["version"] == "1.0.0"


async def test_only_the_machine_that_holds_the_run_can_give_it_back(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    other = await rig.second_agent()
    assert (await other.job_call(job["id"], "release")).status_code == 404
    await rig.agent.job_call(job["id"], "start")
    r = await rig.agent.job_call(job["id"], "release")  # running: too late to give back
    assert r.status_code == 409


async def test_a_run_the_panel_asked_to_cancel_ends_cancelled_when_given_back(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    cancel = await rig.panel.admin_a.post(
        f"/jobs/{job['id']}/cancel", headers=csrf(rig.panel.admin_a)
    )
    assert cancel.status_code == 200, cancel.text
    r = await rig.agent.job_call(job["id"], "release")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "cancelled"


# --- a run assigned to an agent that is gone ---------------------------------------------------


async def _age_assignment(rig: Rig, job_id: str, seconds: int) -> None:
    async with tenant_session(
        rig.panel.env.app.state.session_factory, tenant_id=rig.panel.tenant_a
    ) as db:
        await db.execute(
            text("UPDATE jobs SET assigned_at = now() - make_interval(secs => :s) WHERE id = :j"),
            {"s": seconds, "j": uuid.UUID(job_id)},
        )


async def test_a_run_assigned_to_an_agent_that_is_gone_goes_back_to_the_queue(rig: Rig) -> None:
    """A long poll answered after the agent died leaves a run assigned to an online machine that
    nobody will start. The heartbeat, which says what the agent holds, frees it."""
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    await _age_assignment(rig, job["id"], 600)
    assert (await rig.agent.heartbeat()).status_code == 200  # holds nothing
    detail = (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()
    assert detail["status"] == "pending" and detail["machine_id"] is None
    again = (await rig.agent.next_job()).json()  # and it can be taken again
    assert again["job_id"] == job["id"]


async def test_a_run_the_agent_holds_or_just_took_is_left_alone(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    # Just taken: the agent may be about to start it.
    assert (await rig.agent.heartbeat()).status_code == 200
    assert (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "assigned"
    # Old, but the agent says it is the one it holds.
    await _age_assignment(rig, job["id"], 600)
    assert (await rig.agent.heartbeat(current_job_id=job["id"])).status_code == 200
    assert (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()["status"] == "assigned"


async def test_a_machine_of_another_client_cannot_give_back_a_run_of_this_one(
    rig: Rig, signing: SigningKey
) -> None:
    """The cross-client case of `release`, with a run that really is assigned: client B's machine
    gets a 404 and the run of A stays exactly as it was."""
    job = await run(rig.panel.admin_a, rig.bot["id"])
    await _take(rig)
    b_client = new_client(rig.panel.env.app)
    try:
        bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
        agent_b = await enrolled_agent(
            rig.panel, rig.panel.tenant_b, rig.panel.admin_b, bot_b["pool_id"], b_client
        )
        assert (await agent_b.job_call(job["id"], "release")).status_code == 404
    finally:
        await b_client.aclose()
    detail = (await rig.panel.admin_a.get(f"/jobs/{job['id']}")).json()
    assert detail["status"] == "assigned" and detail["machine_id"] == rig.agent.machine_id
