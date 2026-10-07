"""Screenshots: pre-signed upload straight to the S3 and a short-lived view (M3, ADR 0019)."""

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import httpx
import pytest
from botocore.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import S3Env
from .helpers import csrf, new_client
from .jobs_helpers import Rig, make_bot, run, set_job

pytestmark = pytest.mark.s3

Factory = async_sessionmaker[AsyncSession]
PNG = b"\x89PNG\r\n\x1a\n" + bytes(56)  # 64 bytes


async def _running(rig: Rig) -> str:
    job = await run(rig.panel.admin_a, rig.bot["id"])
    assert (await rig.agent.next_job()).json()["job_id"] == job["id"]
    await rig.agent.job_call(job["id"], "start")
    return str(job["id"])


async def _upload(rig: Rig, job_id: str, data: bytes = PNG) -> str:
    """The whole agent side: presign, PUT straight to the storage, confirm."""
    r = await rig.agent.presign(job_id, size_bytes=len(data))
    assert r.status_code == 200, r.text
    body = r.json()
    async with httpx.AsyncClient() as plain:  # no cookies, no token: the URL is the credential
        put = await plain.put(body["url"], content=data, headers=body["headers"])
    assert put.status_code == 200, put.text
    done = await rig.agent.uploaded(body["artifact_id"])
    assert done.status_code == 200, done.text
    return str(body["artifact_id"])


def _admin_s3(s3: S3Env, bucket: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=s3.endpoint_url,
        aws_access_key_id=s3.access_key_id,
        aws_secret_access_key=s3.secret_access_key,
        region_name=s3.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


# --- the happy path ---------------------------------------------------------------------------


async def test_upload_then_view_without_a_permanent_address(rig: Rig) -> None:
    job_id = await _running(rig)
    artifact_id = await _upload(rig, job_id)

    listed = (await rig.panel.admin_a.get(f"/jobs/{job_id}/artifacts")).json()["items"]
    assert [i["id"] for i in listed] == [artifact_id]
    assert listed[0]["content_type"] == "image/png" and listed[0]["size_bytes"] == 64
    assert "url" not in listed[0] and "storage_key" not in listed[0]

    r = await rig.panel.admin_a.get(f"/artifacts/{artifact_id}/content")
    assert r.status_code == 302
    assert r.headers["cache-control"] == "no-store"
    location = r.headers["location"]
    assert "X-Amz-Signature=" in location and "X-Amz-Expires=60" in location
    async with httpx.AsyncClient() as plain:
        image = await plain.get(location)
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert image.headers["content-disposition"] == "inline"


async def test_the_object_key_is_built_by_the_server(rig: Rig) -> None:
    job_id = await _running(rig)
    artifact_id = await _upload(rig, job_id)
    async with tenant_session(
        rig.panel.env.app.state.session_factory, tenant_id=rig.panel.tenant_a
    ) as db:
        key: str = (
            await db.execute(
                text("SELECT storage_key FROM artifacts WHERE id = :a"), {"a": artifact_id}
            )
        ).scalar_one()
    assert key == f"tenants/{rig.panel.tenant_a}/jobs/{job_id}/{artifact_id}.png"
    jpg = (await rig.agent.presign(job_id, content_type="image/jpeg", size_bytes=10)).json()
    assert re.search(r"/jobs/[0-9a-f-]+/[0-9a-f-]+\.jpg\?", jpg["url"])


async def test_any_role_of_the_client_and_the_staff_view_it(rig: Rig) -> None:
    artifact_id = await _upload(rig, await _running(rig))
    for client in (rig.panel.operator_a, rig.panel.viewer_a):
        assert (await client.get(f"/artifacts/{artifact_id}/content")).status_code == 302
    await rig.panel.staff.put(
        "/auth/context", json={"client_id": None}, headers=csrf(rig.panel.staff)
    )
    assert (await rig.panel.staff.get(f"/artifacts/{artifact_id}/content")).status_code == 302


async def test_the_view_address_expires(rig: Rig) -> None:
    app = rig.panel.env.app
    app.state.settings = app.state.settings.model_copy(update={"artifact_view_seconds": 1})
    artifact_id = await _upload(rig, await _running(rig))
    location = (await rig.panel.admin_a.get(f"/artifacts/{artifact_id}/content")).headers[
        "location"
    ]
    await asyncio.sleep(3)
    async with httpx.AsyncClient() as plain:
        assert (await plain.get(location)).status_code >= 400


# --- what the agent may ask -------------------------------------------------------------------


async def test_presign_is_bounded(rig: Rig) -> None:
    job_id = await _running(rig)
    too_big = await rig.agent.presign(job_id, size_bytes=5_000_001)
    assert too_big.status_code == 422 and too_big.json()["detail"]["code"] == "artifact_too_large"
    for bad in (
        {"size_bytes": 0},
        {"size_bytes": -1},
        {"content_type": "text/html"},
        {"content_type": "image/svg+xml"},
        {"kind": "file"},
    ):
        assert (await rig.agent.presign(job_id, **bad)).status_code == 422, bad
    extra = await rig.agent.client.post(
        "/agent/artifacts/presign",
        json={
            "job_id": job_id,
            "kind": "screenshot",
            "content_type": "image/png",
            "size_bytes": 1,
            "storage_key": "tenants/x/y.png",
        },
        headers=rig.agent._auth(),
    )
    assert extra.status_code == 422, "the key is never taken from the agent"


async def test_a_run_has_a_limit_of_captures(rig: Rig) -> None:
    app = rig.panel.env.app
    app.state.settings = app.state.settings.model_copy(update={"artifact_max_per_job": 2})
    job_id = await _running(rig)
    for _ in range(2):
        assert (await rig.agent.presign(job_id)).status_code == 200
    third = await rig.agent.presign(job_id)
    assert third.status_code == 409 and third.json()["detail"]["code"] == "artifact_limit"


async def test_only_the_machine_of_the_run_and_only_recent_runs(rig: Rig) -> None:
    job_id = await _running(rig)
    other = await rig.second_agent()
    assert (await other.presign(job_id)).status_code == 404
    assert (await other.presign(str(uuid.uuid4()))).status_code == 404

    bot_b = await make_bot(rig.panel, rig.panel.tenant_b, rig.panel.admin_b)
    job_b = await run(rig.panel.admin_b, bot_b["id"])
    assert (await rig.agent.presign(job_b["id"])).status_code == 404

    await rig.agent.job_call(job_id, "complete")
    assert (await rig.agent.presign(job_id)).status_code == 200  # still in the grace period
    await set_job(
        rig.panel, rig.panel.tenant_a, job_id, finished_at=datetime.now(UTC) - timedelta(minutes=6)
    )
    late = await rig.agent.presign(job_id)
    assert late.status_code == 409 and late.json()["detail"]["code"] == "job_closed"


# --- the storage refuses what was not announced ---------------------------------------------


async def test_the_storage_refuses_a_body_of_another_size_or_type(rig: Rig) -> None:
    job_id = await _running(rig)
    body = (await rig.agent.presign(job_id, size_bytes=64)).json()
    async with httpx.AsyncClient() as plain:
        bigger = await plain.put(body["url"], content=PNG + b"x", headers=body["headers"])
        other_type = await plain.put(
            body["url"], content=PNG, headers={"Content-Type": "text/html"}
        )
    assert bigger.status_code >= 400 and other_type.status_code >= 400
    # Nothing arrived, so confirming is refused and the artifact stays unusable.
    never = await rig.agent.uploaded(body["artifact_id"])
    assert never.status_code == 409 and never.json()["detail"]["code"] == "upload_not_found"
    assert (
        await rig.panel.admin_a.get(f"/artifacts/{body['artifact_id']}/content")
    ).status_code == 404
    assert (await rig.panel.admin_a.get(f"/jobs/{job_id}/artifacts")).json()["items"] == []


async def test_confirming_checks_the_object_itself(rig: Rig, s3_env: S3Env) -> None:
    job_id = await _running(rig)
    body = (await rig.agent.presign(job_id, size_bytes=64)).json()
    key = re.search(r"/(tenants/[^?]+)\?", body["url"]).group(1)  # type: ignore[union-attr]
    bucket = rig.panel.env.app.state.settings.s3_bucket
    # Somebody with storage access (not the agent's URL) puts something else there.
    _admin_s3(s3_env, bucket).put_object(
        Bucket=bucket, Key=key, Body=b"not-what-was-announced", ContentType="image/png"
    )
    r = await rig.agent.uploaded(body["artifact_id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_mismatch"
    assert (
        await rig.panel.admin_a.get(f"/artifacts/{body['artifact_id']}/content")
    ).status_code == 404
    gone = _admin_s3(s3_env, bucket).list_objects_v2(Bucket=bucket, Prefix=key)
    assert gone.get("KeyCount", 0) == 0, "the mismatched object was removed"


async def test_confirming_is_idempotent_and_only_for_the_machines_own(rig: Rig) -> None:
    job_id = await _running(rig)
    artifact_id = await _upload(rig, job_id)
    assert (await rig.agent.uploaded(artifact_id)).status_code == 200
    other = await rig.second_agent()
    assert (await other.uploaded(artifact_id)).status_code == 404
    assert (await rig.agent.uploaded(str(uuid.uuid4()))).status_code == 404


# --- isolation of the view --------------------------------------------------------------------


async def test_other_clients_and_anonymous_callers_cannot_view_it(rig: Rig) -> None:
    artifact_id = await _upload(rig, await _running(rig))
    assert (await rig.panel.admin_b.get(f"/artifacts/{artifact_id}/content")).status_code == 404
    anonymous = new_client(rig.panel.env.app)
    try:
        assert (await anonymous.get(f"/artifacts/{artifact_id}/content")).status_code == 401
    finally:
        await anonymous.aclose()
    # A machine token is not a session either.
    r = await rig.agent.client.get(f"/artifacts/{artifact_id}/content", headers=rig.agent._auth())
    assert r.status_code == 401
    # And the list of another client's run is not found.
    job_id = (await rig.panel.admin_a.get("/jobs")).json()["items"][0]["id"]
    assert (await rig.panel.admin_b.get(f"/jobs/{job_id}/artifacts")).status_code == 404
