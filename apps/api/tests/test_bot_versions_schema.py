"""Database-level guarantees of bot_versions and what hangs off it (M4, ADR 0021)."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import Seed
from .test_jobs_schema import _bot, _job, _pool

Factory = async_sessionmaker[AsyncSession]


async def _version(
    s: AsyncSession,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    *,
    version: str = "1.0.0",
    status: str = "published",
    **fields: object,
) -> uuid.UUID:
    cols: dict[str, object] = {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "version": version,
        "package_sha256": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "size_bytes": 1234,
        "signature": b"\x01" * 64,
        "key_id": "0123456789abcdef",
        "manifest": json.dumps({"version": version}),
        "storage_key": f"tenants/{tenant_id}/bots/{bot_id}/versions/{uuid.uuid4()}.rgpkg",
        "status": status,
        "upload_expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "published_at": datetime.now(UTC) if status == "published" else None,
    }
    cols.update(fields)
    names = ", ".join(cols)
    marks = ", ".join(f"CAST(:{c} AS jsonb)" if c == "manifest" else f":{c}" for c in cols)
    r = await s.execute(
        text(f"INSERT INTO bot_versions ({names}) VALUES ({marks}) RETURNING id"),  # noqa: S608
        cols,
    )
    return r.scalar_one()


async def _world(factory: Factory, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    async with tenant_session(factory, tenant_id=tenant_id) as s:
        pool = await _pool(s, tenant_id)
        bot = await _bot(s, tenant_id, pool)
        other_bot = await _bot(s, tenant_id, pool)
        version = await _version(s, tenant_id, bot)
        other_version = await _version(s, tenant_id, other_bot)
    return {
        "pool": pool,
        "bot": bot,
        "other_bot": other_bot,
        "version": version,
        "other_version": other_version,
    }


async def test_each_tenant_sees_only_its_own_versions(app_factory: Factory, seed: Seed) -> None:
    await _world(app_factory, seed.tenant_a)
    await _world(app_factory, seed.tenant_b)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        foreign: int = (
            await s.execute(
                text("SELECT count(*) FROM bot_versions WHERE tenant_id <> :t"),
                {"t": seed.tenant_a},
            )
        ).scalar_one()
        own: int = (await s.execute(text("SELECT count(*) FROM bot_versions"))).scalar_one()
    assert foreign == 0 and own > 0
    async with tenant_session(app_factory) as s:
        assert (await s.execute(text("SELECT count(*) FROM bot_versions"))).scalar_one() == 0


async def test_a_platform_admin_reads_versions_but_writes_only_in_the_tenant_context(
    app_factory: Factory, seed: Seed
) -> None:
    w = await _world(app_factory, seed.tenant_b)
    async with tenant_session(app_factory, platform_admin=True) as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM bot_versions WHERE id = :v"), {"v": w["version"]}
            )
        ).scalar_one() == 1
        with pytest.raises(DBAPIError):
            await _version(s, seed.tenant_b, w["bot"], version="9.9.9")


async def test_a_version_cannot_belong_to_a_bot_of_another_client(
    app_factory: Factory, seed: Seed
) -> None:
    b = await _world(app_factory, seed.tenant_b)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        with pytest.raises(DBAPIError, match="fk_bot_versions_tenant_bot_bots"):
            await _version(s, seed.tenant_a, b["bot"], version="2.0.0")


async def test_the_database_refuses_a_version_in_use_from_another_bot_or_client(
    app_factory: Factory, seed: Seed
) -> None:
    a = await _world(app_factory, seed.tenant_a)
    b = await _world(app_factory, seed.tenant_b)

    async def put_in_use(tenant: uuid.UUID, bot: uuid.UUID, version: uuid.UUID) -> None:
        async with tenant_session(app_factory, tenant_id=tenant) as s:
            await s.execute(
                text("UPDATE bots SET current_version_id = :v WHERE id = :b"),
                {"v": version, "b": bot},
            )

    await put_in_use(seed.tenant_a, a["bot"], a["version"])  # its own: fine
    with pytest.raises(DBAPIError, match=r"fk_bots_tenant_current_version|published version"):
        await put_in_use(seed.tenant_a, a["bot"], a["other_version"])  # another bot
    with pytest.raises(DBAPIError, match=r"fk_bots_tenant_current_version|published version"):
        await put_in_use(seed.tenant_a, a["bot"], b["version"])  # another client


async def test_only_a_published_version_can_be_in_use(app_factory: Factory, seed: Seed) -> None:
    a = await _world(app_factory, seed.tenant_a)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        pending = await _version(s, seed.tenant_a, a["bot"], version="3.0.0", status="uploading")
    with pytest.raises(DBAPIError, match="published version"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            await s.execute(
                text("UPDATE bots SET current_version_id = :v WHERE id = :b"),
                {"v": pending, "b": a["bot"]},
            )


async def test_a_run_can_only_carry_a_version_of_its_own_bot(
    app_factory: Factory, seed: Seed
) -> None:
    a = await _world(app_factory, seed.tenant_a)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        await _job(s, seed.tenant_a, a["bot"], a["pool"], bot_version_id=a["version"])
    with pytest.raises(DBAPIError, match="fk_jobs_tenant_bot_version"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            await _job(s, seed.tenant_a, a["bot"], a["pool"], bot_version_id=a["other_version"])


async def test_a_published_version_never_changes_and_an_upload_only_moves_on(
    app_factory: Factory, seed: Seed
) -> None:
    a = await _world(app_factory, seed.tenant_a)
    with pytest.raises(DBAPIError, match="cannot change"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            await s.execute(
                text(
                    "UPDATE bot_versions SET status = 'expired', published_at = NULL WHERE id = :v"
                ),
                {"v": a["version"]},
            )
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        up = await _version(s, seed.tenant_a, a["bot"], version="4.0.0", status="uploading")
        await s.execute(
            text(
                "UPDATE bot_versions SET status = 'published', published_at = now() WHERE id = :v"
            ),
            {"v": up},
        )


async def test_the_app_can_only_change_the_state_of_an_upload_and_never_delete(
    app_factory: Factory, seed: Seed
) -> None:
    a = await _world(app_factory, seed.tenant_a)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        up = await _version(s, seed.tenant_a, a["bot"], version="5.0.0", status="uploading")
    for sql in (
        "UPDATE bot_versions SET package_sha256 = repeat('0', 64) WHERE id = :v",
        "UPDATE bot_versions SET release_note = 'x' WHERE id = :v",
        "UPDATE bot_versions SET signature = '\\x00' WHERE id = :v",
        "DELETE FROM bot_versions WHERE id = :v",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
                await s.execute(text(sql), {"v": up})


async def test_a_version_number_is_unique_per_bot_unless_the_upload_expired(
    app_factory: Factory, seed: Seed
) -> None:
    a = await _world(app_factory, seed.tenant_a)
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        old = await _version(s, seed.tenant_a, a["bot"], version="6.0.0", status="uploading")
    with pytest.raises(DBAPIError, match="uq_bot_versions_bot_version"):
        async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
            await _version(s, seed.tenant_a, a["bot"], version="6.0.0")
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        await s.execute(
            text("UPDATE bot_versions SET status = 'expired' WHERE id = :v"), {"v": old}
        )
        await _version(s, seed.tenant_a, a["bot"], version="6.0.0")
        # The same number on another bot is fine.
        await _version(s, seed.tenant_a, a["other_bot"], version="6.0.0")


async def test_bot_version_checks(app_factory: Factory, seed: Seed) -> None:
    a = await _world(app_factory, seed.tenant_a)
    bad: list[dict[str, Any]] = [
        {"version": "1.0"},
        {"version": "v1.0.0"},
        {"package_sha256": "ABC"},
        {"size_bytes": 0},
        {"size_bytes": 600_000_000},
        {"signature": b"\x01" * 63},
        {"key_id": "not-a-key-id"},
        {"status": "weird"},
        {"status": "published", "published_at": None},
        {"status": "uploading", "published_at": datetime.now(UTC)},
        {"release_note": "x" * 2001},
        {"manifest": json.dumps({"x": "a" * 17000})},
    ]
    for n, fields in enumerate(bad):
        with pytest.raises(DBAPIError, match="violates"):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
                await _version(s, seed.tenant_a, a["bot"], **{"version": f"7.0.{n}", **fields})


async def test_job_error_codes_and_reasons(app_factory: Factory, seed: Seed) -> None:
    a = await _world(app_factory, seed.tenant_a)
    finished = {"status": "failed", "finished_at": datetime.now(UTC)}
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        for code in (
            "package_invalid",
            "robot_not_allowed",
            "runtime_missing",
            "environment_failed",
            "robot_host_unavailable",
        ):
            await _job(s, seed.tenant_a, a["bot"], a["pool"], error_code=code, **finished)
        await _job(
            s,
            seed.tenant_a,
            a["bot"],
            a["pool"],
            error_code="package_invalid",
            error_reason="hash_mismatch",
            **finished,
        )
    for fields in (
        {"error_code": "package_invalid", "error_reason": "because"},
        {"error_code": "robot_failed", "error_reason": "hash_mismatch"},
        {"error_reason": "hash_mismatch"},
    ):
        with pytest.raises(DBAPIError, match="violates"):
            async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
                await _job(s, seed.tenant_a, a["bot"], a["pool"], **fields, **finished)


async def test_machines_start_with_the_local_kill_switch_off(
    app_factory: Factory, seed: Seed
) -> None:
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as s:
        pool = await _pool(s, seed.tenant_a)
        paused: bool = (
            await s.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name) VALUES (:t, :p, :n)"
                    " RETURNING paused_locally"
                ),
                {"t": seed.tenant_a, "p": pool, "n": f"m-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
    assert paused is False


async def test_the_foreign_key_alone_also_refuses_a_version_of_another_bot(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    """Two layers guard "the version in use is this bot's": the trigger (friendlier message) and
    the composite foreign key. With the trigger off, the key must still say no."""
    a = await _world(app_factory, seed.tenant_a)
    with pytest.raises(DBAPIError, match="fk_bots_tenant_current_version"):
        async with tenant_session(owner_factory, tenant_id=seed.tenant_a) as s:
            await s.execute(text("ALTER TABLE bots DISABLE TRIGGER bots_current_version_published"))
            await s.execute(
                text("UPDATE bots SET current_version_id = :v WHERE id = :b"),
                {"v": a["other_version"], "b": a["bot"]},
            )
