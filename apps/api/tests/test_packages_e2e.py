"""Signed packages with the real thing: API, worker and a real agent as separate processes, a real
Postgres and S3, a real `uv`. The scenarios of the milestone:

- the agent runs a signed version of a robot;
- a tampered package is refused and the panel can show why (the code and reason on the run);
- a package signed for another client is refused even though the server served it;
- a robot that is not on the machine's local list does not run;
- the kill switch keeps new runs from starting, and the panel only gets an indication of it;
- switching the version in use makes the next run use the new one.

Publishing goes straight into the database and the bucket (the routes have their own tests): the
point here is what the agent does with what the server hands it, including a server that lies.
"""

import asyncio
import base64
import json
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pytest
import pytest_asyncio
from botocore.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import DbUrls, S3Env, Seed
from .package_helpers import SignedPackage, SigningKey, make_package
from .test_agent_e2e import Stack, _eventually, running_stack
from .test_jobs_e2e import Rigged, _no_secrets

pytestmark = [pytest.mark.e2e, pytest.mark.s3]

Factory = async_sessionmaker[AsyncSession]


def robot(label: str) -> bytes:
    return f"print('INFO versao {label}', flush=True)\n".encode()


@dataclass
class World:
    stack: Stack
    factory: Factory
    s3: Any
    bucket: str
    package: str
    signing: SigningKey


@pytest_asyncio.fixture
async def world(
    db_urls: DbUrls,
    seed: Seed,
    app_factory: Factory,
    tmp_path: Path,
    s3_env: S3Env,
    signing: SigningKey,
) -> AsyncIterator[World]:
    bucket = f"e2e-{uuid.uuid4().hex[:10]}"
    package = f"pk_{uuid.uuid4().hex[:8]}"
    api_env = {
        "REGISTA_S3_ENDPOINT_URL": s3_env.endpoint_url,
        "REGISTA_S3_ACCESS_KEY_ID": s3_env.access_key_id,
        "REGISTA_S3_SECRET_ACCESS_KEY": s3_env.secret_access_key,
        "REGISTA_S3_REGION": s3_env.region,
        "REGISTA_S3_BUCKET": bucket,
        "REGISTA_AGENT_POLL_WAIT_SECONDS": "5",
        "REGISTA_DEV_TRUSTED_KEYS": str(signing.keys_file),
    }
    # `Rigged` names the bot after the one folder in this directory; no robot lives in it. The
    # agent runs only what the server hands it, as a signed package.
    bots_dir = tmp_path / "bots"
    (bots_dir / package).mkdir(parents=True)
    agent_extra = {
        "REGISTA_ENVIRONMENT": "dev",
        "REGISTA_DEV_TRUSTED_KEYS": str(signing.keys_file),
        "REGISTA_DEV_PYTHON": sys.executable,  # stands in for what `regista-agent setup` installs
        "REGISTA_DEV_BOTS_DIR": str(bots_dir),
        "REGISTA_CANCEL_GRACE_SECONDS": "2",
        "REGISTA_LOG_FLUSH_SECONDS": "0.3",
        "REGISTA_POLL_WAIT_SECONDS": "5",
        "REGISTA_JOB_PRIORITY": "normal",
    }
    s3 = boto3.client(
        "s3",
        endpoint_url=s3_env.endpoint_url,
        aws_access_key_id=s3_env.access_key_id,
        aws_secret_access_key=s3_env.secret_access_key,
        region_name=s3_env.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    async with running_stack(
        db_urls, seed, app_factory, tmp_path, api_env=api_env, agent_extra=agent_extra
    ) as stack:
        # The API creates the bucket at startup (dev); the agent reads nothing else from here.
        yield World(stack, app_factory, s3, bucket, package, signing)


async def _rig(world: World, *, allow: bool = True) -> Rigged:
    if allow:
        done = world.stack.agent_blocking("allow", world.package)
        assert done.returncode == 0, done.stdout + done.stderr
    rig = Rigged(world.stack, world.factory)
    await rig.setup()
    assert rig.bot_id
    return rig


async def _publish(
    world: World,
    rig: Rigged,
    version: str,
    label: str,
    *,
    activate: bool = True,
    tenant_id: uuid.UUID | None = None,
) -> tuple[str, SignedPackage]:
    """What publishing leaves behind: the object in the bucket and a published row."""
    tenant = world.stack.tenant_id
    pkg = make_package(
        world.signing,
        tenant_id=tenant_id or tenant,
        package_name=world.package,
        version=version,
        members={"bot/main.py": robot(label)},
    )
    version_id = uuid.uuid4()
    key = f"tenants/{tenant}/bots/{rig.bot_id}/versions/{version_id}.rgpkg"
    world.s3.put_object(
        Bucket=world.bucket, Key=key, Body=pkg.package, ContentType="application/octet-stream"
    )
    document = json.loads(pkg.signature)

    async with tenant_session(world.factory, tenant_id=tenant) as db:
        await db.execute(
            text(
                "INSERT INTO bot_versions (id, tenant_id, bot_id, version, package_sha256,"
                " size_bytes, signature, key_id, manifest, storage_key, status,"
                " upload_expires_at, published_at) VALUES (:id, :t, :b, :v, :sha, :size, :sig,"
                " :kid, CAST(:m AS jsonb), :k, 'published', now(), now())"
            ),
            {
                "id": version_id,
                "t": tenant,
                "b": uuid.UUID(rig.bot_id),
                "v": version,
                "sha": pkg.sha256,
                "size": len(pkg.package),
                "sig": base64.b64decode(document["signature"]),
                "kid": world.signing.key_id,
                "m": json.dumps(document["manifest"], sort_keys=True),
                "k": key,
            },
        )
        if activate:
            await db.execute(
                text("UPDATE bots SET current_version_id = :v WHERE id = :b"),
                {"v": version_id, "b": uuid.UUID(rig.bot_id)},
            )
    return str(version_id), pkg


async def _use(world: World, rig: Rigged, version_id: str) -> None:
    async with tenant_session(world.factory, tenant_id=world.stack.tenant_id) as db:
        await db.execute(
            text("UPDATE bots SET current_version_id = :v WHERE id = :b"),
            {"v": uuid.UUID(version_id), "b": uuid.UUID(rig.bot_id)},
        )


async def _messages(rig: Rigged, job_id: str) -> list[str]:
    return [line["message"] for line in await rig.logs(job_id)]


# --- the scenarios ----------------------------------------------------------------------------


async def test_the_agent_runs_a_signed_version(world: World) -> None:
    rig = await _rig(world)
    try:
        await _publish(world, rig, "1.0.0", "1.0.0")
        job_id = await rig.run("ok")
        await rig.wait_status(job_id, "completed", seconds=60)
        assert "versao 1.0.0" in await _messages(rig, job_id)
        assert (await rig.job(job_id))["bot_version"] == "1.0.0"
        _no_secrets(world.stack, [])
    finally:
        rig.kill_leftovers()


async def test_a_tampered_package_is_refused_and_the_run_says_why(world: World) -> None:
    rig = await _rig(world)
    try:
        _, pkg = await _publish(world, rig, "1.0.0", "1.0.0")
        # Someone with access to the bucket swaps the file for another of the same size.
        key = world.s3.list_objects_v2(Bucket=world.bucket, Prefix="tenants/")["Contents"][0]["Key"]
        swapped = bytearray(pkg.package)
        swapped[len(swapped) // 2] ^= 0xFF
        world.s3.put_object(Bucket=world.bucket, Key=key, Body=bytes(swapped))

        job_id = await rig.run("ok")
        await rig.wait_status(job_id, "failed", seconds=60)
        job = await rig.job(job_id)
        assert job["error_code"] == "package_invalid" and job["error_reason"] == "hash_mismatch"
        assert not job["error_message"], "the panel gets the code and the reason, not free text"
        messages = await _messages(rig, job_id)
        assert "versao 1.0.0" not in messages, "the robot never started"
        assert any("hash_mismatch" in m for m in messages), "the detail is in the run's logs"
    finally:
        rig.kill_leftovers()


async def test_a_package_for_another_client_is_refused_even_when_the_server_serves_it(
    world: World, seed: Seed
) -> None:
    """The server hands this machine a perfectly signed package... of client B. The signature is
    fine and the hash matches; it is still not this machine's."""
    rig = await _rig(world)
    try:
        await _publish(world, rig, "1.0.0", "of-client-b", tenant_id=seed.tenant_b)
        job_id = await rig.run("ok")
        await rig.wait_status(job_id, "failed", seconds=60)
        job = await rig.job(job_id)
        assert job["error_code"] == "package_invalid" and job["error_reason"] == "wrong_client"
        assert "versao of-client-b" not in await _messages(rig, job_id)
    finally:
        rig.kill_leftovers()


async def test_a_robot_that_is_not_on_the_local_list_does_not_run(world: World) -> None:
    rig = await _rig(world, allow=False)  # nobody ran `regista-agent allow`
    try:
        await _publish(world, rig, "1.0.0", "1.0.0")
        job_id = await rig.run("ok")
        await rig.wait_status(job_id, "failed", seconds=60)
        job = await rig.job(job_id)
        assert job["error_code"] == "robot_not_allowed" and job["error_reason"] is None
        assert "versao 1.0.0" not in await _messages(rig, job_id)

        # Releasing it on the machine is all it takes, and the next run goes.
        done = world.stack.agent_blocking("allow", world.package)
        assert done.returncode == 0, done.stdout + done.stderr
        assert rig.agent is not None
        rig.agent.stop()
        # A long poll of the old agent may still be open on the server; let it end.
        await asyncio.sleep(6)
        rig.start_agent("agent-2.log")
        again = await rig.run("ok")
        await rig.wait_status(again, "completed", seconds=60)
    finally:
        rig.kill_leftovers()


async def test_the_kill_switch_stops_new_runs_and_the_panel_only_hears_about_it(
    world: World,
) -> None:
    rig = await _rig(world)
    try:
        await _publish(world, rig, "1.0.0", "1.0.0")
        warm = await rig.run("ok")
        await rig.wait_status(warm, "completed", seconds=60)

        paused = world.stack.agent_blocking("pause")
        assert paused.returncode == 0, paused.stdout + paused.stderr

        async def indicated() -> bool:
            return bool((await rig.machine())["paused_locally"])

        await _eventually(indicated, seconds=30, what="the panel to show the machine as paused")

        job_id = await rig.run("ok")
        await asyncio.sleep(8)  # longer than one poll of the agent
        assert (await rig.job(job_id))["status"] == "pending", "a paused agent takes nothing"

        resumed = world.stack.agent_blocking("resume")
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        await rig.wait_status(job_id, "completed", seconds=60)

        async def cleared() -> bool:
            return not (await rig.machine())["paused_locally"]

        await _eventually(cleared, seconds=30, what="the indication to go away")
    finally:
        rig.kill_leftovers()


async def test_switching_the_version_in_use_makes_the_next_run_use_it(world: World) -> None:
    rig = await _rig(world)
    try:
        await _publish(world, rig, "1.0.0", "1.0.0")
        first = await rig.run("ok")
        await rig.wait_status(first, "completed", seconds=60)
        assert "versao 1.0.0" in await _messages(rig, first)

        second_id, _ = await _publish(world, rig, "1.1.0", "1.1.0", activate=False)
        # Published but not in use: the next run is still 1.0.0.
        still = await rig.run("ok")
        await rig.wait_status(still, "completed", seconds=60)
        assert "versao 1.0.0" in await _messages(rig, still)

        await _use(world, rig, second_id)
        next_run = await rig.run("ok")
        await rig.wait_status(next_run, "completed", seconds=60)
        messages = await _messages(rig, next_run)
        assert "versao 1.1.0" in messages and "versao 1.0.0" not in messages
        assert (await rig.job(next_run))["bot_version"] == "1.1.0"
    finally:
        rig.kill_leftovers()
