import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(url: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID | None = None,
    platform_admin: bool = False,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to a tenant (docs/specs/security.md).

    `set_config(..., true)` is transaction-local, so nothing leaks to the next user of
    the pooled connection. Commits on success and rolls back on error. With no
    `tenant_id`, RLS returns zero rows. `platform_admin` only widens reads.
    """
    async with factory() as session, session.begin():
        if tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
            )
        if platform_admin:
            await session.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))
        yield session
