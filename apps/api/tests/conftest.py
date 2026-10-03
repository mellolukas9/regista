import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from regista_api.auth import mfa
from regista_api.core.config import Settings
from regista_api.core.db import create_engine, create_session_factory, tenant_session
from regista_api.core.keys import LocalKeyProvider
from regista_api.core.rls import tenant_rls_statements
from regista_api.main import create_app

from .helpers import Env, FakeClock

# The Ryuk reaper container races with Docker Desktop port publishing on Windows. The
# container below is stopped by its context manager instead.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

REPO_ROOT = Path(__file__).resolve().parents[3]
INITDB_SCRIPT = REPO_ROOT / "infra" / "compose" / "initdb" / "01-roles.sh"
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"

SUPERUSER_PASSWORD = "test-superuser"
OWNER_PASSWORD = "test-owner"
APP_PASSWORD = "test-app"


@dataclass(frozen=True)
class DbUrls:
    owner: str
    app: str


TEST_MASTER_KEY = LocalKeyProvider.generate_key()


def make_settings(db_urls: DbUrls, **overrides: object) -> Settings:
    """Settings for tests: real test database, in-memory e-mail, throwaway master key."""
    values: dict[str, object] = {
        "environment": "test",
        "database_url": db_urls.app,
        "database_owner_url": db_urls.owner,
        "master_key": TEST_MASTER_KEY,
        "email_backend": "memory",
        # Every test shares one client address, so the per-IP limits stay out of the way unless
        # a test sets them on purpose.
        "rate_login_ip_per_minute": 100_000,
        "rate_invite_ip_per_minute": 100_000,
        "rate_agent_enroll_ip_per_minute": 100_000,
        "rate_agent_auth_ip_per_minute": 100_000,
        "rate_agent_auth_machine_per_minute": 100_000,
        **overrides,
    }
    return Settings.model_validate(values)


@dataclass(frozen=True)
class Seed:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Runs the app lifespan (ASGITransport doesn't) and yields a client bound to it."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@contextmanager
def _postgres() -> Iterator[DbUrls]:
    """A throwaway Postgres 18; roles come from the same init script used in dev."""
    container = (
        PostgresContainer(
            "postgres:18",
            username="postgres",
            password=SUPERUSER_PASSWORD,
            dbname="regista",
            driver=None,
        )
        .with_env("REGISTA_OWNER_PASSWORD", OWNER_PASSWORD)
        .with_env("REGISTA_APP_PASSWORD", APP_PASSWORD)
        .with_volume_mapping(str(INITDB_SCRIPT), "/docker-entrypoint-initdb.d/01-roles.sh", "ro")
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)

        def url(user: str, password: str) -> str:
            return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/regista"

        yield DbUrls(
            owner=url("regista_owner", OWNER_PASSWORD), app=url("regista_app", APP_PASSWORD)
        )


@pytest.fixture(scope="session")
def db_urls() -> Iterator[DbUrls]:
    """Real Postgres 18 shared by the whole test session."""
    with _postgres() as urls:
        yield urls


@pytest_asyncio.fixture(scope="module")
async def empty_db_urls() -> AsyncIterator[DbUrls]:
    """A second, migrated and still empty database, for tests that need a clean slate
    (the dev seed refuses to run when clients exist)."""
    with _postgres() as urls:
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", urls.owner)
        await asyncio.to_thread(command.upgrade, config, "head")
        yield urls


@pytest_asyncio.fixture(scope="session")
async def owner_engine(db_urls: DbUrls) -> AsyncIterator[AsyncEngine]:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", db_urls.owner)
    # env.py drives its own event loop, so run it off the test loop.
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_engine(db_urls.owner)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE rls_probe ("
                " id uuid PRIMARY KEY DEFAULT uuidv7(),"
                " tenant_id uuid NOT NULL REFERENCES tenants(id),"
                " label text NOT NULL)"
            )
        )
        for statement in tenant_rls_statements("rls_probe"):
            await conn.execute(text(statement))
        await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON rls_probe TO regista_app"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def app_engine(db_urls: DbUrls, owner_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    # Single connection on purpose: proves tenant context never leaks through the pool.
    engine = create_engine(db_urls.app, pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def app_factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(app_engine)


@pytest.fixture(scope="session")
def owner_factory(owner_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(owner_engine)


@pytest_asyncio.fixture(scope="session")
async def seed(app_factory: async_sessionmaker[AsyncSession]) -> Seed:
    """Two tenants (created as platform admin) with two probe rows each."""
    ids: dict[str, uuid.UUID] = {}
    async with tenant_session(app_factory, platform_admin=True) as session:
        for slug in ("a", "b"):
            result = await session.execute(
                text(
                    "INSERT INTO tenants (name, slug, data_region)"
                    " VALUES (:name, :slug, 'sa-east-1') RETURNING id"
                ),
                {"name": f"Tenant {slug.upper()}", "slug": slug},
            )
            ids[slug] = result.scalar_one()

    for slug, tid in ids.items():
        async with tenant_session(app_factory, tenant_id=tid) as session:
            for n in (1, 2):
                await session.execute(
                    text("INSERT INTO rls_probe (tenant_id, label) VALUES (:t, :l)"),
                    {"t": tid, "l": f"{slug}{n}"},
                )
    return Seed(tenant_a=ids["a"], tenant_b=ids["b"])


@asynccontextmanager
async def open_env(db_urls: DbUrls, **overrides: object) -> AsyncIterator[Env]:
    """A running app (lifespan included), an HTTP client and a controllable TOTP clock."""
    clock = FakeClock()
    original = mfa._clock
    mfa._clock = clock
    try:
        app = create_app(make_settings(db_urls, **overrides))
        async with api_client(app) as client:
            yield Env(app=app, client=client, clock=clock)
    finally:
        mfa._clock = original


@pytest_asyncio.fixture
async def env(db_urls: DbUrls, seed: Seed) -> AsyncIterator[Env]:
    async with open_env(db_urls) as e:
        yield e


@pytest_asyncio.fixture(scope="session")
async def internal_tenant(app_factory: async_sessionmaker[AsyncSession], seed: Seed) -> uuid.UUID:
    """The single hidden Artemisys tenant (created as platform admin)."""
    async with tenant_session(app_factory, platform_admin=True) as session:
        result = await session.execute(
            text(
                "INSERT INTO tenants (name, slug, data_region, is_internal)"
                " VALUES ('Artemisys', 'artemisys-internal', 'sa-east-1', true) RETURNING id"
            )
        )
        return result.scalar_one()
