"""The `regista-admin` operations and the development seed."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api import admin
from regista_api.auth import mfa
from regista_api.auth.deps import AppState
from regista_api.cli import build_parser
from regista_api.core.db import create_engine, create_session_factory, tenant_session
from regista_api.core.email import MemoryEmailSender
from regista_api.core.keys import LocalKeyProvider
from regista_api.main import create_app

from .conftest import TEST_MASTER_KEY, DbUrls, Seed, api_client, make_settings, open_env
from .helpers import Account, FakeClock, csrf, login, new_client, onboard, unique_email

Factory = async_sessionmaker[AsyncSession]


@asynccontextmanager
async def _state(urls: DbUrls, **settings: object) -> AsyncIterator[tuple[Factory, AppState]]:
    cfg = make_settings(urls, **settings)
    engine = create_engine(cfg.database_url)
    factory = create_session_factory(engine)
    try:
        yield (
            factory,
            AppState(
                settings=cfg,
                factory=factory,
                keys=LocalKeyProvider(TEST_MASTER_KEY),
                email=MemoryEmailSender(),
            ),
        )
    finally:
        await engine.dispose()


def _mail(state: AppState) -> MemoryEmailSender:
    assert isinstance(state.email, MemoryEmailSender)
    return state.email


async def _row(factory: Factory, internal: uuid.UUID, email: str) -> dict[str, object]:
    async with tenant_session(factory, tenant_id=internal) as db:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT id, status, role, is_platform_admin, display_name, password_hash,"
                        " mfa_enabled FROM users WHERE email = :e"
                    ),
                    {"e": email},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


# --- command line -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["create-platform-admin", "--email", "a@artemisys.com.br", "--name", "A"],
        ["resend-platform-admin-invite", "--email", "a@artemisys.com.br"],
        ["remove-platform-admin", "--email", "a@artemisys.com.br"],
        ["seed-dev"],
    ],
)
def test_cli_has_no_way_to_pass_a_password(argv: list[str]) -> None:
    parser = build_parser()
    assert parser.parse_args(argv).command == argv[0]
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--password", "segredo"])


# --- platform admins --------------------------------------------------------------------------


async def test_create_platform_admin_invites_and_onboards(
    db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    email = unique_email("staff")
    async with _state(db_urls) as (factory, state):
        await admin.create_platform_admin(factory, state, email=email.upper(), name="Pessoa Teste")
        row = await _row(factory, internal_tenant, email)
        assert (row["status"], row["role"], row["is_platform_admin"]) == (
            "invited",
            "tenant_admin",
            True,
        )
        assert row["display_name"] == "Pessoa Teste"
        assert row["password_hash"] is None
        message = _mail(state).outbox[-1]
        assert message.to == email
        assert "/invite#" in message.text

        with pytest.raises(admin.AdminError, match="já existe"):
            await admin.create_platform_admin(factory, state, email=email, name="Outra")

    # The invitation really works and ends in a platform admin session.
    async with open_env(db_urls) as env:
        token = message.text.split("/invite#")[1].split()[0]
        accept = await env.client.post(
            "/auth/invitations/accept",
            json={"token": token, "password": "uma-senha-bem-longa-e-unica-1"},
        )
        assert accept.status_code == 200
        setup = (await env.client.post("/auth/mfa/setup", headers=csrf(env.client))).json()
        activate = await env.client.post(
            "/auth/mfa/activate",
            json={"code": env.clock.code(setup["secret"])},
            headers=csrf(env.client),
        )
        assert activate.status_code == 200
        env.clock.next_step()
        await env.client.post("/auth/recovery-codes/ack", headers=csrf(env.client))
        me = (await env.client.get("/auth/me")).json()
        assert me["is_platform_admin"] is True
        assert me["display_name"] == "Pessoa Teste"
        assert me["context"]["all_clients"] is True


async def test_resend_platform_admin_invite_resets_credentials(
    db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    async with open_env(db_urls) as env:
        account = await onboard(
            env.app,
            env.client,
            internal_tenant,
            env.clock,
            role="tenant_admin",
            platform_admin=True,
        )
        async with _state(db_urls) as (factory, state):
            await admin.resend_platform_admin_invite(factory, state, email=account.email)
            row = await _row(factory, internal_tenant, account.email)
            assert (row["status"], row["password_hash"], row["mfa_enabled"]) == (
                "invited",
                None,
                False,
            )
            assert _mail(state).outbox[-1].subject == "Novo convite para o Regista"
            with pytest.raises(admin.AdminError, match="Não existe"):
                await admin.resend_platform_admin_invite(
                    factory, state, email="ninguem@example.com"
                )
        assert (await env.client.get("/auth/me")).status_code == 401  # sessions are gone
        async with new_client(env.app) as browser:
            r = await browser.post(
                "/auth/login", json={"email": account.email, "password": account.password}
            )
            assert r.status_code == 401


async def test_the_last_active_platform_admin_cannot_be_removed(
    db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    async with open_env(db_urls) as env, _state(db_urls) as (factory, _):
        # Park every other active platform admin (other tests created some) for the duration.
        async with tenant_session(factory, tenant_id=internal_tenant) as db:
            parked = [
                r[0]
                for r in await db.execute(
                    text("SELECT id FROM users WHERE is_platform_admin AND status = 'active'")
                )
            ]
            await db.execute(
                text("UPDATE users SET status = 'invited' WHERE id = ANY(:ids)"), {"ids": parked}
            )
        try:
            only = await onboard(
                env.app,
                env.client,
                internal_tenant,
                env.clock,
                role="tenant_admin",
                platform_admin=True,
            )
            with pytest.raises(admin.AdminError, match="último administrador"):
                await admin.remove_platform_admin(factory, email=only.email)
            assert (await _row(factory, internal_tenant, only.email))["status"] == "active"

            # With a second active one, removal works and ends the person's sessions.
            async with new_client(env.app) as other:
                second = await onboard(
                    env.app,
                    other,
                    internal_tenant,
                    env.clock,
                    role="tenant_admin",
                    platform_admin=True,
                )
                await admin.remove_platform_admin(factory, email=only.email)
                assert (await env.client.get("/auth/me")).status_code == 401
                assert (await other.get("/auth/me")).status_code == 200
                await admin.remove_platform_admin(factory, email=only.email)  # idempotent
                assert second.email
            row = await _row(factory, internal_tenant, only.email)
            assert row["status"] == "disabled"
        finally:
            async with tenant_session(factory, tenant_id=internal_tenant) as db:
                await db.execute(
                    text("UPDATE users SET status = 'active' WHERE id = ANY(:ids)"), {"ids": parked}
                )


async def test_removing_a_client_user_is_not_a_cli_job(
    db_urls: DbUrls, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    """The CLI only touches platform admins: a client user's e-mail is not found by it."""
    async with open_env(db_urls) as env, _state(db_urls) as (factory, state):
        person = await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
        with pytest.raises(admin.AdminError, match="Não existe"):
            await admin.remove_platform_admin(factory, email=person.email)
        with pytest.raises(admin.AdminError, match="Não existe"):
            await admin.resend_platform_admin_invite(factory, state, email=person.email)
        assert (await env.client.get("/auth/me")).status_code == 200


# --- development seed -------------------------------------------------------------------------


async def test_seed_dev_refuses_outside_dev_and_when_clients_exist(
    db_urls: DbUrls, seed: Seed
) -> None:
    async with _state(db_urls, environment="test") as (factory, state):
        with pytest.raises(admin.AdminError, match="REGISTA_ENVIRONMENT=dev"):
            await admin.seed_dev(factory, state)
    async with _state(db_urls, environment="dev") as (factory, state):
        with pytest.raises(admin.AdminError, match="banco vazio"):
            await admin.seed_dev(factory, state)


async def test_seed_dev_builds_the_sample_data(empty_db_urls: DbUrls) -> None:
    async with _state(empty_db_urls, environment="dev") as (factory, state):
        result = await admin.seed_dev(factory, state)

        async with tenant_session(factory, platform_admin=True) as db:
            tenants = (
                await db.execute(
                    text("SELECT name, is_internal, created_at FROM tenants ORDER BY created_at")
                )
            ).all()
        assert [(t.name, t.is_internal) for t in tenants] == [
            ("Artemisys (demonstração)", False),
            ("Escritório Exemplo", False),
            ("Artemisys", True),
        ] or {t.name for t in tenants} == {
            "Artemisys",
            "Artemisys (demonstração)",
            "Escritório Exemplo",
        }
        by_name = {t.name: t.created_at for t in tenants}
        assert (by_name["Escritório Exemplo"].year, by_name["Escritório Exemplo"].month) == (
            2026,
            8,
        )
        assert (
            by_name["Artemisys (demonstração)"].year,
            by_name["Artemisys (demonstração)"].month,
        ) == (2026, 7)

        async with tenant_session(factory, platform_admin=True) as db:
            seeded_bots = (
                await db.execute(
                    text(
                        "SELECT b.name, b.package_name, p.name AS pool, t.name AS client"
                        " FROM bots b JOIN pools p ON p.id = b.pool_id"
                        " JOIN tenants t ON t.id = b.tenant_id"
                    )
                )
            ).all()
        assert [tuple(r) for r in seeded_bots] == [
            (
                "Busca na Wikipédia",
                "demo_busca_wikipedia",
                "Artemisys – Demonstração",  # noqa: RUF001  (the design-system sample name)
                "Artemisys (demonstração)",
            )
        ]

        emails = {u.email for u in result.users}
        assert emails == {
            "equipe@artemisys.example.com",
            "admin@escritorio-exemplo.com.br",
            "operacao@escritorio-exemplo.com.br",
            "financeiro@escritorio-exemplo.com.br",
            "estagio@escritorio-exemplo.com.br",
        }
        # Random credentials, never the same twice, nothing fixed in the code.
        passwords = [u.password for u in result.users if u.password]
        assert len(passwords) == len(set(passwords)) == 4
        assert all(len(p) >= 20 for p in passwords)
        assert _mail(state).outbox[-1].to == "estagio@escritorio-exemplo.com.br"

        with pytest.raises(admin.AdminError, match="banco vazio"):
            await admin.seed_dev(factory, state)

    # The seeded accounts can really sign in with what was printed.

    clock = FakeClock()
    original = mfa._clock
    mfa._clock = clock
    try:
        app = create_app(make_settings(empty_db_urls, environment="dev"))
        async with api_client(app) as client:
            by_email = {u.email: u for u in result.users}
            for email, role, staff in (
                ("admin@escritorio-exemplo.com.br", "tenant_admin", False),
                ("operacao@escritorio-exemplo.com.br", "operator", False),
                ("equipe@artemisys.example.com", "tenant_admin", True),
            ):
                seeded = by_email[email]
                assert seeded.password and seeded.totp_secret
                account = Account(
                    uuid.uuid4(), uuid.uuid4(), email, seeded.password, seeded.totp_secret
                )
                async with new_client(app) as browser:
                    clock.next_step()
                    assert (await login(browser, account, clock)).status_code == 200
                    me = (await browser.get("/auth/me")).json()
                    assert (me["role"], me["is_platform_admin"]) == (role, staff)
                    if email.startswith("admin@"):
                        assert me["tenant_name"] == "Escritório Exemplo"
                        assert me["recovery_codes_remaining"] == 10

            # "Sem MFA": password works, but the session must finish MFA setup first.
            finance = by_email["financeiro@escritorio-exemplo.com.br"]
            r = await client.post(
                "/auth/login", json={"email": finance.email, "password": finance.password}
            )
            assert r.json() == {"stage": "mfa_setup"}
    finally:
        mfa._clock = original
