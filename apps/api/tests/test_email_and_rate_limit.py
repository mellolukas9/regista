import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.auth.emails import (
    invitation_email,
    invitation_link,
    password_changed_email,
    reinvitation_email,
)
from regista_api.auth.rate_limit import client_ip, rate_limited
from regista_api.core.config import ConfigurationError, Settings
from regista_api.core.db import tenant_session
from regista_api.core.email import (
    ConsoleEmailSender,
    EmailMessage,
    MemoryEmailSender,
    create_email_sender,
)
from regista_api.main import create_app

from .conftest import DbUrls, api_client, make_settings

Factory = async_sessionmaker[AsyncSession]


# --- e-mail -----------------------------------------------------------------------------------


async def test_memory_sender_keeps_messages() -> None:
    sender = MemoryEmailSender()
    message = EmailMessage(to="a@example.com", subject="s", text="t")
    await sender.send(message)
    assert sender.outbox == [message]


async def test_console_sender_prints_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    await ConsoleEmailSender().send(EmailMessage(to="a@example.com", subject="Oi", text="corpo"))
    out = capsys.readouterr().out
    assert "a@example.com" in out
    assert "Oi" in out
    assert "corpo" in out


def test_sender_is_chosen_by_settings(db_urls: DbUrls) -> None:
    assert isinstance(create_email_sender(make_settings(db_urls)), MemoryEmailSender)
    assert isinstance(
        create_email_sender(make_settings(db_urls, email_backend="console")), ConsoleEmailSender
    )


def test_invitation_link_puts_the_token_in_the_fragment() -> None:
    link = invitation_link("https://painel.example.com/", "tok123")
    assert link == "https://painel.example.com/invite#tok123"


def test_email_templates() -> None:
    link = "https://x/invite#t"
    invite = invitation_email(to="a@example.com", link=link, days=7)
    again = reinvitation_email(to="a@example.com", link=link, days=7)
    changed = password_changed_email(to="a@example.com")
    assert invite.subject == "Seu acesso ao Regista"
    assert again.subject == "Novo convite para o Regista"
    assert changed.subject == "Sua senha do Regista foi alterada"
    assert link in invite.text
    assert link in again.text
    assert "7 dias" in invite.text
    assert "http" not in changed.text


# --- rate limit -------------------------------------------------------------------------------


async def test_rate_limited_counts_per_scope_and_subject(app_factory: Factory) -> None:
    subject = f"{uuid.uuid4()}@example.com"
    results = [
        await rate_limited(
            app_factory, scope="login-email", subject=subject, window_seconds=900, max_hits=3
        )
        for _ in range(5)
    ]
    assert results == [False, False, False, True, True]

    # Another subject, and another scope for the same subject, have their own counters.
    assert not await rate_limited(
        app_factory,
        scope="login-email",
        subject="other@example.com",
        window_seconds=900,
        max_hits=3,
    )
    assert not await rate_limited(
        app_factory, scope="mfa", subject=subject, window_seconds=900, max_hits=3
    )


async def test_rate_limited_ignores_case_and_stores_no_clear_text(
    app_factory: Factory, owner_factory: Factory
) -> None:
    subject = f"Case-{uuid.uuid4()}@Example.com"
    await rate_limited(app_factory, scope="s", subject=subject, window_seconds=60, max_hits=1)
    assert await rate_limited(
        app_factory, scope="s", subject=subject.lower(), window_seconds=60, max_hits=1
    )
    # Even the table owner (FORCE RLS) cannot read it without the gate; the key is a digest anyway.
    async with tenant_session(owner_factory) as session:
        visible: int = (
            await session.execute(text("SELECT count(*) FROM auth_rate_limits"))
        ).scalar_one()
        assert visible == 0


def _request(peer: str | None, forwarded: str | None) -> Request:
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    fake = SimpleNamespace(client=SimpleNamespace(host=peer) if peer else None, headers=headers)
    return cast(Request, fake)


def test_client_ip_ignores_forwarded_header_from_untrusted_peers(db_urls: DbUrls) -> None:
    settings = make_settings(db_urls)
    assert client_ip(_request("203.0.113.9", "1.2.3.4"), settings) == "203.0.113.9"
    assert client_ip(_request(None, None), settings) is None


def test_client_ip_reads_forwarded_header_from_the_right(db_urls: DbUrls) -> None:
    settings = make_settings(db_urls)
    # The client injected 6.6.6.6; the trusted proxy appended the real peer 198.51.100.7.
    assert client_ip(_request("127.0.0.1", "6.6.6.6, 198.51.100.7"), settings) == "198.51.100.7"
    assert client_ip(_request("127.0.0.1", None), settings) == "127.0.0.1"
    assert client_ip(_request("127.0.0.1", "198.51.100.7, 127.0.0.1"), settings) == "198.51.100.7"


# --- app wiring: headers, 422 body, startup validation ----------------------------------------


async def test_security_headers(db_urls: DbUrls) -> None:
    async with api_client(create_app(make_settings(db_urls))) as client:
        health = await client.get("/health")
        auth = await client.get("/auth/anything")
        account = await client.get("/account/anything")
    assert health.headers["x-content-type-options"] == "nosniff"
    assert "cache-control" not in health.headers
    for response in (auth, account):
        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"


async def test_validation_errors_do_not_echo_the_input(db_urls: DbUrls) -> None:
    app = create_app(make_settings(db_urls))

    @app.post("/echo-probe")
    async def probe(body: dict[str, Any]) -> None:  # pragma: no cover - never reached
        raise AssertionError

    async with api_client(app) as client:
        response = await client.post("/echo-probe", content="not json: hunter2-secret")
    assert response.status_code == 422
    assert "hunter2-secret" not in response.text
    assert "input" not in response.json()["detail"][0]


def test_startup_requires_a_master_key(db_urls: DbUrls) -> None:
    with pytest.raises(ConfigurationError, match="REGISTA_MASTER_KEY"):
        create_app(make_settings(db_urls, master_key=""))


def test_prod_refuses_console_and_memory_email(db_urls: DbUrls) -> None:
    for backend in ("console", "memory"):
        settings: Settings = make_settings(db_urls, environment="prod", email_backend=backend)
        with pytest.raises(ConfigurationError, match="dev and tests only"):
            create_app(settings)
