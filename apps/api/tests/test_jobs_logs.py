"""Run logs: batches from the agent, reading in the panel, limits, redaction (M3)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from regista_api.core.db import tenant_session

from .helpers import csrf
from .jobs_helpers import Rig, make_bot, run, set_job

Factory = async_sessionmaker[AsyncSession]


def _line(
    seq: int, message: str = "ola", level: str = "INFO", ts: datetime | None = None
) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": (ts or datetime.now(UTC)).isoformat(),
        "level": level,
        "message": message,
    }


async def _running(rig: Rig) -> str:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).json()["job_id"] == job["id"]
    await rig.agent.job_call(job["id"], "start")
    return str(job["id"])


async def _read(
    rig: Rig, job_id: str, client: httpx.AsyncClient | None = None, **query: str | int
) -> dict[str, Any]:
    r = await (client or rig.panel.admin_a).get(f"/jobs/{job_id}/logs", params=query)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


# --- storing and reading ----------------------------------------------------------------------


async def test_lines_are_stored_in_order_and_read_by_the_panel(rig: Rig) -> None:
    job_id = await _running(rig)
    sent = await rig.agent.send_logs(
        job_id, [_line(3, "terceira", "ERROR"), _line(1, "primeira"), _line(2, "segunda", "WARN")]
    )
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"accepted": 3, "truncated": False}

    page = await _read(rig, job_id)
    assert [(i["seq"], i["message"], i["level"]) for i in page["items"]] == [
        (1, "primeira", "INFO"),
        (2, "segunda", "WARN"),
        (3, "terceira", "ERROR"),
    ]
    assert page["level_counts"] == {"INFO": 1, "WARN": 1, "ERROR": 1}
    assert page["has_more"] is False

    # Follow a live run: only what is new, and one level at a time.
    assert [i["seq"] for i in (await _read(rig, job_id, after_seq=2))["items"]] == [3]
    only_warn = await _read(rig, job_id, level="WARN")
    assert [i["seq"] for i in only_warn["items"]] == [2]
    assert only_warn["level_counts"]["ERROR"] == 1  # the counts stay whole-run
    paged = await _read(rig, job_id, limit=2)
    assert [i["seq"] for i in paged["items"]] == [1, 2] and paged["has_more"] is True


async def test_resending_a_batch_does_not_duplicate_lines(rig: Rig) -> None:
    job_id = await _running(rig)
    batch = [_line(1, "a"), _line(2, "b")]
    for _ in range(3):
        assert (await rig.agent.send_logs(job_id, batch)).status_code == 200
    assert len((await _read(rig, job_id))["items"]) == 2


async def test_any_role_of_the_client_reads_logs_and_the_staff_too(rig: Rig) -> None:
    job_id = await _running(rig)
    await rig.agent.send_logs(job_id, [_line(1, "visivel")])
    for client in (rig.panel.operator_a, rig.panel.viewer_a):
        assert len((await _read(rig, job_id, client))["items"]) == 1
    await rig.panel.staff.put(
        "/auth/context", json={"client_id": None}, headers=csrf(rig.panel.staff)
    )
    assert len((await _read(rig, job_id, rig.panel.staff))["items"]) == 1


async def test_markup_in_a_log_line_comes_back_as_plain_text(rig: Rig) -> None:
    job_id = await _running(rig)
    payload = "<script>alert(1)</script> <img src=x onerror=alert(2)>"
    await rig.agent.send_logs(job_id, [_line(1, payload)])
    r = await rig.panel.admin_a.get(f"/jobs/{job_id}/logs")
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.json()["items"][0]["message"] == payload  # escaping is the panel's job


# --- what is cleaned --------------------------------------------------------------------------


async def test_secrets_and_control_sequences_never_get_stored(
    rig: Rig, owner_factory: Factory
) -> None:
    job_id = await _running(rig)
    dirty = "\x1b[31mfalha\x1b[0m com rga1.AAAA.BBBB e token=hunter2 e Bearer abc123 e rgk_zzzz\x00"
    await rig.agent.send_logs(job_id, [_line(1, dirty)])
    message = (await _read(rig, job_id))["items"][0]["message"]
    for leaked in ("rga1.AAAA", "hunter2", "abc123", "rgk_zzzz", "\x1b", "\x00"):
        assert leaked not in message
    assert "falha" in message
    # And not in the table either, read as the owner.
    async with tenant_session(owner_factory, tenant_id=rig.panel.tenant_a) as db:
        stored: str = (
            await db.execute(text("SELECT string_agg(message, ' ') FROM job_logs"))
        ).scalar_one()
    assert "hunter2" not in stored and "rga1.AAAA" not in stored


async def test_a_long_line_is_cut_and_marked(rig: Rig) -> None:
    job_id = await _running(rig)
    await rig.agent.send_logs(job_id, [_line(1, "é" * 6000)])  # 12 000 bytes
    message = (await _read(rig, job_id))["items"][0]["message"]
    assert len(message.encode()) <= 4096
    assert message.endswith("…[cortado]")


async def test_timestamps_outside_the_life_of_the_run_are_pulled_in(rig: Rig) -> None:
    job_id = await _running(rig)
    now = datetime.now(UTC)
    await rig.agent.send_logs(
        job_id,
        [
            _line(1, "futuro", ts=now + timedelta(days=4000)),
            _line(2, "passado", ts=now - timedelta(days=4000)),
        ],
    )
    items = (await _read(rig, job_id))["items"]
    stamps = {i["message"]: datetime.fromisoformat(i["ts"]) for i in items}
    assert stamps["futuro"] < now + timedelta(minutes=2)
    assert stamps["passado"] > now - timedelta(minutes=5)


# --- who may write ----------------------------------------------------------------------------


async def test_only_the_machine_of_the_run_may_send_its_logs(rig: Rig) -> None:
    job_id = await _running(rig)
    other = await rig.second_agent()
    assert (await other.send_logs(job_id, [_line(1)])).status_code == 404
    assert (await other.send_logs(str(uuid.uuid4()), [_line(1)])).status_code == 404

    bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
    job_b = await run(rig.panel.admin_b, bot_b["id"])
    assert (await rig.agent.send_logs(job_b["id"], [_line(1)])).status_code == 404
    assert (await _read(rig, job_id))["items"] == []
    assert (await rig.panel.admin_b.get(f"/jobs/{job_id}/logs")).status_code == 404


async def test_logs_are_accepted_for_a_few_minutes_after_the_run_ends(rig: Rig) -> None:
    job_id = await _running(rig)
    assert (await rig.agent.job_call(job_id, "complete")).status_code == 200
    assert (await rig.agent.send_logs(job_id, [_line(1, "ultima linha")])).status_code == 200
    await set_job(
        rig.panel, rig.panel.tenant_a, job_id, finished_at=datetime.now(UTC) - timedelta(minutes=6)
    )
    late = await rig.agent.send_logs(job_id, [_line(2, "tarde demais")])
    assert late.status_code == 409 and late.json()["detail"]["code"] == "job_closed"
    assert [i["message"] for i in (await _read(rig, job_id))["items"]] == ["ultima linha"]


async def test_a_pending_run_has_no_machine_so_nobody_may_write_to_it(rig: Rig) -> None:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.send_logs(job["id"], [_line(1)])).status_code == 404


# --- limits -----------------------------------------------------------------------------------


async def test_a_run_has_a_log_budget_and_says_so_once(rig: Rig) -> None:
    app = rig.panel.env.app
    app.state.settings = app.state.settings.model_copy(update={"log_job_max_lines": 5})
    job_id = await _running(rig)
    first = await rig.agent.send_logs(job_id, [_line(n, f"linha {n}") for n in range(1, 9)])
    assert first.json() == {"accepted": 5, "truncated": True}
    again = await rig.agent.send_logs(job_id, [_line(9, "mais uma")])
    assert again.json() == {"accepted": 0, "truncated": True}
    items = (await _read(rig, job_id))["items"]
    messages = [i["message"] for i in items]
    assert messages[:5] == [f"linha {n}" for n in range(1, 6)]
    assert sum("Limite de logs" in m for m in messages) == 1
    assert items[-1]["level"] == "WARN"


async def test_the_byte_budget_counts_too(rig: Rig) -> None:
    app = rig.panel.env.app
    app.state.settings = app.state.settings.model_copy(update={"log_job_max_bytes": 100})
    job_id = await _running(rig)
    r = await rig.agent.send_logs(job_id, [_line(1, "x" * 60), _line(2, "y" * 60)])
    assert r.json() == {"accepted": 1, "truncated": True}


async def test_the_request_is_bounded(rig: Rig) -> None:
    job_id = await _running(rig)
    many = await rig.agent.send_logs(job_id, [_line(n) for n in range(201)])
    assert many.status_code == 422
    assert (await rig.agent.send_logs(job_id, [])).status_code == 422
    assert (await rig.agent.send_logs(job_id, [_line(n) for n in range(200)])).status_code == 200

    bad_level = await rig.agent.send_logs(job_id, [_line(1, level="DEBUG")])
    assert bad_level.status_code == 422
    extra = await rig.agent.client.post(
        "/agent/logs",
        json={"job_id": job_id, "lines": [_line(1)], "tenant_id": str(rig.panel.tenant_b)},
        headers=rig.agent._auth(),
    )
    assert extra.status_code == 422, "the tenant is never taken from the body"

    huge = await rig.agent.send_logs(job_id, [_line(1, "x" * 8000)] * 200)
    assert huge.status_code == 413  # more than 256 KB is refused unread
    # The other agent routes keep their small limit.
    tiny = await rig.agent.client.post(
        "/agent/heartbeat", content=b"x" * 20_000, headers=rig.agent._auth()
    )
    assert tiny.status_code == 413


# --- a missing partition is never silent ------------------------------------------------------


async def test_a_missing_partition_fails_loudly_and_loses_nothing(
    rig: Rig, owner_factory: Factory
) -> None:
    job_id = await _running(rig)
    old = datetime(2025, 1, 15, 12, tzinfo=UTC)
    # A run created back then, so lines of that month are legitimate (no partition for it).
    await set_job(rig.panel, rig.panel.tenant_a, job_id, created_at=old)
    line = _line(1, "janeiro", ts=old + timedelta(hours=1))
    with capture_logs() as captured:
        # `now` is today, so the line is pulled up to the lowest allowed time: still an old month
        # only if the run is old. Send with the current time instead to hit today's partition:
        r = await rig.agent.send_logs(job_id, [line])
    # The clamp keeps a line within [created_at - 1 min, now + 1 min], so January stays January.
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == {"code": "log_partition_missing", "months": ["2025-01"]}
    errors = [e for e in captured if e["event"] == "job_logs_partition_missing"]
    assert errors and errors[0]["log_level"] == "error" and errors[0]["months"] == ["2025-01"]
    assert (await _read(rig, job_id))["items"] == []

    # The partition appears (the worker made it); the agent retries and nothing was lost.
    async with tenant_session(owner_factory) as db:
        await db.execute(
            text(
                "CREATE TABLE job_logs_202501 PARTITION OF job_logs"
                " FOR VALUES FROM ('2025-01-01+00') TO ('2025-02-01+00')"
            )
        )
    try:
        retry = await rig.agent.send_logs(job_id, [line])
        assert retry.status_code == 200, retry.text
        assert [i["message"] for i in (await _read(rig, job_id))["items"]] == ["janeiro"]
    finally:
        async with tenant_session(owner_factory) as db:
            await db.execute(text("DROP TABLE job_logs_202501"))
