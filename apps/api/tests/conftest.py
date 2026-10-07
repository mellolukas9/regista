import asyncio
import os
import sys
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
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from regista_api.auth import mfa
from regista_api.core.config import Settings
from regista_api.core.db import create_engine, create_session_factory, tenant_session
from regista_api.core.keys import LocalKeyProvider
from regista_api.core.rls import tenant_rls_statements
from regista_api.main import create_app

from .helpers import Env, FakeClock, Panel, new_client, onboard, unique_email
from .jobs_helpers import Rig, enrolled_agent, make_bot
from .package_helpers import SigningKey, new_signing_key

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


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """psycopg (the job queue's driver) cannot run on Windows' default Proactor loop; asyncpg
    works on both, so the whole suite uses the selector loop there."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


class _TestSettings(Settings):
    """No `.env`: a developer's own file (S3 keys, a master key...) must never change what a test
    says about the settings."""

    model_config = SettingsConfigDict(env_file=None)


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
    return _TestSettings.model_validate(values)


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


@pytest.fixture(scope="session")
def signing(tmp_path_factory: pytest.TempPathFactory) -> SigningKey:
    """A throwaway key that signs the packages of the tests. The apps trust it through the
    development-only override; the production key never exists in a test."""
    return new_signing_key(tmp_path_factory.mktemp("signing"))


@pytest_asyncio.fixture
async def panel(
    request: pytest.FixtureRequest,
    db_urls: DbUrls,
    seed: Seed,
    internal_tenant: uuid.UUID,
    signing: SigningKey,
) -> AsyncIterator[Panel]:
    """Browsers of client A (admin, operator, viewer), of client B (admin) and of the staff.

    A test (or module) marked `s3` also gets a running SeaweedFS and an app configured for it."""
    overrides: dict[str, object] = {"dev_trusted_keys": str(signing.keys_file)}
    if request.node.get_closest_marker("s3") is not None:
        s3: S3Env = request.getfixturevalue("s3_env")
        overrides |= {
            "s3_endpoint_url": s3.endpoint_url,
            "s3_access_key_id": s3.access_key_id,
            "s3_secret_access_key": s3.secret_access_key,
            "s3_region": s3.region,
            "s3_bucket": f"test-{uuid.uuid4().hex[:12]}",
        }
    async with open_env(db_urls, **overrides) as env:
        plan = (
            ("admin_a", seed.tenant_a, "tenant_admin", False),
            ("operator_a", seed.tenant_a, "operator", False),
            ("viewer_a", seed.tenant_a, "viewer", False),
            ("admin_b", seed.tenant_b, "tenant_admin", False),
            ("staff", internal_tenant, "tenant_admin", True),
        )
        clients: dict[str, httpx.AsyncClient] = {}
        for name, tenant, role, platform in plan:
            clients[name] = new_client(env.app)
            await onboard(
                env.app,
                clients[name],
                tenant,
                env.clock,
                role=role,
                email=unique_email(name.replace("_", "-")),
                platform_admin=platform,
            )
        try:
            yield Panel(env=env, tenant_a=seed.tenant_a, tenant_b=seed.tenant_b, **clients)
        finally:
            for client in clients.values():
                await client.aclose()


@pytest_asyncio.fixture
async def rig(panel: Panel) -> AsyncIterator[Rig]:
    """Client A with a pool, a bot and an online machine that has a simulated agent."""
    agent_client = new_client(panel.env.app)  # an agent never has a session cookie
    bot = await make_bot(panel, panel.tenant_a, panel.admin_a)
    agent = await enrolled_agent(panel, panel.tenant_a, panel.admin_a, bot["pool_id"], agent_client)
    try:
        yield Rig(panel, bot, agent, agent_client)
    finally:
        await agent_client.aclose()


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


# --- S3 (SeaweedFS, ADR 0019) ------------------------------------------------------------------

S3_CONFIG = REPO_ROOT / "infra" / "compose" / "seaweedfs" / "s3.json"
S3_IMAGE = "chrislusf/seaweedfs:4.48"
S3_ACCESS_KEY = "regista-dev-access"
S3_SECRET_KEY = "regista-dev-secret-not-for-production"


@dataclass(frozen=True)
class S3Env:
    endpoint_url: str
    access_key_id: str = S3_ACCESS_KEY
    secret_access_key: str = S3_SECRET_KEY
    region: str = "us-east-1"


@pytest.fixture(scope="session")
def s3_env() -> Iterator[S3Env]:
    """A throwaway SeaweedFS with the same identity file as the dev compose."""
    import time

    import httpx as _httpx
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(S3_IMAGE)
        # Every bucket is a collection with volumes of its own, and the tests make one each: on a
        # small disk the default limit of volumes runs out and PutObject answers InternalError.
        .with_command(
            "server -dir=/data -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json"
            " -volume.max=2000 -master.volumeSizeLimitMB=64"
        )
        .with_volume_mapping(str(S3_CONFIG), "/etc/seaweedfs/s3.json", "ro")
        .with_exposed_ports(8333)
    )
    with container:
        deadline = time.monotonic() + 60
        endpoint = ""
        while True:
            try:
                # Docker Desktop publishes the port a moment after the container starts.
                if not endpoint:
                    host = container.get_container_host_ip()
                    endpoint = f"http://{host}:{container.get_exposed_port(8333)}"
                # Without credentials the S3 port answers 403 once it is up.
                if _httpx.get(endpoint, timeout=2).status_code in (200, 403):
                    break
            except (_httpx.HTTPError, ConnectionError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("SeaweedFS did not start in 60 s")
            time.sleep(0.5)
        yield S3Env(endpoint_url=endpoint)
