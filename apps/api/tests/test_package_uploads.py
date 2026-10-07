"""Uploads that were started and never finished are expired by the worker, and their objects go."""

import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.bots import uploads
from regista_api.core.db import tenant_session

from .conftest import Seed
from .test_bot_versions_schema import _version
from .test_jobs_schema import _bot, _pool

Factory = async_sessionmaker[AsyncSession]


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.deleted: list[str] = []
        self.fail = fail

    async def delete(self, key: str) -> None:
        if self.fail:
            raise OSError("storage is down")
        self.deleted.append(key)


async def _insert(
    factory: Factory, tenant: uuid.UUID, version: str, status: str, age: str
) -> tuple[uuid.UUID, str]:
    async with tenant_session(factory, tenant_id=tenant) as s:
        pool = await _pool(s, tenant)
        bot = await _bot(s, tenant, pool)
        key = f"tenants/{tenant}/bots/{bot}/versions/{uuid.uuid4()}.rgpkg"
        vid = await _version(s, tenant, bot, version=version, status=status, storage_key=key)
    return vid, key


async def _age(owner: Factory, tenant: uuid.UUID, vid: uuid.UUID, interval: timedelta) -> None:
    async with tenant_session(owner, tenant_id=tenant) as s:
        await s.execute(
            text(
                "UPDATE bot_versions SET upload_expires_at = now() - CAST(:i AS interval)"
                " WHERE id = :v"
            ),
            {"i": interval, "v": vid},
        )


async def _status(owner: Factory, tenant: uuid.UUID, vid: uuid.UUID) -> str:
    async with tenant_session(owner, tenant_id=tenant) as s:
        status: str = (
            await s.execute(text("SELECT status FROM bot_versions WHERE id = :v"), {"v": vid})
        ).scalar_one()
    return status


async def test_only_old_unfinished_uploads_of_every_client_are_expired(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    old_a, key_a = await _insert(app_factory, seed.tenant_a, "1.0.0", "uploading", "")
    old_b, key_b = await _insert(app_factory, seed.tenant_b, "1.0.0", "uploading", "")
    recent, _ = await _insert(app_factory, seed.tenant_a, "2.0.0", "uploading", "")
    done, _ = await _insert(app_factory, seed.tenant_a, "3.0.0", "published", "")
    await _age(owner_factory, seed.tenant_a, old_a, timedelta(hours=3))
    await _age(owner_factory, seed.tenant_b, old_b, timedelta(hours=2))
    await _age(
        owner_factory, seed.tenant_a, recent, timedelta(minutes=10)
    )  # past its URL, not an hour

    store = FakeStore()
    expired = await uploads.expire_stale_uploads(app_factory, store)
    assert expired >= 2
    assert {key_a, key_b} <= set(store.deleted)
    assert await _status(owner_factory, seed.tenant_a, old_a) == "expired"
    assert await _status(owner_factory, seed.tenant_b, old_b) == "expired"
    assert await _status(owner_factory, seed.tenant_a, recent) == "uploading"
    assert await _status(owner_factory, seed.tenant_a, done) == "published"

    again = FakeStore()
    await uploads.expire_stale_uploads(app_factory, again)
    assert key_a not in again.deleted, "idempotent: nothing is expired twice"


async def test_a_storage_that_is_down_does_not_stop_the_expiry(
    app_factory: Factory, owner_factory: Factory, seed: Seed
) -> None:
    vid, _ = await _insert(app_factory, seed.tenant_a, "4.0.0", "uploading", "")
    await _age(owner_factory, seed.tenant_a, vid, timedelta(hours=3))
    await uploads.expire_stale_uploads(app_factory, FakeStore(fail=True))
    assert await _status(owner_factory, seed.tenant_a, vid) == "expired"
