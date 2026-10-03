"""Test helpers for the auth flows: invite users, run the onboarding over HTTP, drive TOTP."""

import time
import uuid
from dataclasses import dataclass, field

import httpx
import pyotp
from fastapi import FastAPI
from sqlalchemy import text

from regista_api.auth.invitations import issue_invitation
from regista_api.core.db import tenant_session
from regista_api.core.email import MemoryEmailSender

PASSWORD = "uma-senha-bem-longa-e-unica-1"
NEW_PASSWORD = "outra-senha-bem-longa-e-unica-2"


class FakeClock:
    """Stands in for the TOTP clock so tests can move between 30 s steps without sleeping."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def next_step(self) -> None:
        self.now += 30

    def code(self, secret: str) -> str:
        return pyotp.TOTP(secret).at(int(self.now))


@dataclass
class Account:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    password: str = PASSWORD
    totp_secret: str = ""
    recovery_codes: list[str] = field(default_factory=list)


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("regista_csrf") or ""}


def new_client(app: FastAPI) -> httpx.AsyncClient:
    """Another browser: its own cookie jar, same running app."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


async def invite_user(
    app: FastAPI,
    tenant_id: uuid.UUID,
    *,
    role: str = "viewer",
    email: str | None = None,
    platform_admin: bool = False,
) -> tuple[uuid.UUID, str, str]:
    """Insert an invited user and an invitation; returns (user_id, email, raw token)."""
    email = email or unique_email()
    async with tenant_session(app.state.session_factory, tenant_id=tenant_id) as db:
        user_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO users (tenant_id, email, role, is_platform_admin)"
                    " VALUES (:t, :e, :r, :p) RETURNING id"
                ),
                {"t": tenant_id, "e": email, "r": role, "p": platform_admin},
            )
        ).scalar_one()
        token = await issue_invitation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            created_by=None,
            settings=app.state.settings,
        )
    return user_id, email, token


async def onboard(
    app: FastAPI,
    client: httpx.AsyncClient,
    tenant_id: uuid.UUID,
    clock: FakeClock,
    *,
    role: str = "viewer",
    email: str | None = None,
    platform_admin: bool = False,
) -> Account:
    """Invitation -> password -> TOTP -> recovery codes. The client ends with an active session."""
    user_id, email, token = await invite_user(
        app, tenant_id, role=role, email=email, platform_admin=platform_admin
    )
    accept = await client.post(
        "/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert accept.status_code == 200, accept.text
    setup = await client.post("/auth/mfa/setup", headers=csrf(client))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    activate = await client.post(
        "/auth/mfa/activate", json={"code": clock.code(secret)}, headers=csrf(client)
    )
    assert activate.status_code == 200, activate.text
    codes = activate.json()["recovery_codes"]
    clock.next_step()
    ack = await client.post("/auth/recovery-codes/ack", headers=csrf(client))
    assert ack.status_code == 200, ack.text
    return Account(user_id, tenant_id, email, PASSWORD, secret, codes)


async def login(
    client: httpx.AsyncClient, account: Account, clock: FakeClock, *, password: str | None = None
) -> httpx.Response:
    """Password + TOTP. Returns the verify response."""
    first = await client.post(
        "/auth/login", json={"email": account.email, "password": password or account.password}
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/auth/mfa/verify", json={"code": clock.code(account.totp_secret)}, headers=csrf(client)
    )
    clock.next_step()
    return second


@dataclass
class Env:
    app: FastAPI
    client: httpx.AsyncClient
    clock: FakeClock

    @property
    def mail(self) -> MemoryEmailSender:
        sender = self.app.state.email_sender
        assert isinstance(sender, MemoryEmailSender)
        return sender
