"""No password, TOTP secret, token or recovery code may appear in logs or stdout.

CLAUDE.md, rule 5: secrets never in clear text, not in the database, not in logs.
"""

import logging
import uuid

import httpx
import pytest
import structlog
from sqlalchemy import text

from regista_api.core.db import tenant_session

from .conftest import DbUrls, Seed, open_env
from .helpers import (
    NEW_PASSWORD,
    PASSWORD,
    Account,
    csrf,
    invite_user,
    login,
    new_client,
)

WRONG_PASSWORD = "senha-errada-que-nao-existe-9"


def _remember_cookies(secrets: set[str], client: httpx.AsyncClient) -> None:
    for name in ("regista_session", "regista_csrf"):
        if name in client.cookies:
            secrets.add(client.cookies[name])


async def test_nothing_secret_reaches_logs(
    db_urls: DbUrls,
    seed: Seed,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    secrets: set[str] = {PASSWORD, NEW_PASSWORD, WRONG_PASSWORD}

    async with open_env(db_urls, log_level="DEBUG") as env:
        tenant = seed.tenant_a

        # Invitation -> password -> TOTP -> recovery codes (tokens and codes are secrets).
        user_id, email, invitation_token = await invite_user(env.app, tenant, role="tenant_admin")
        secrets.add(invitation_token)
        await env.client.post("/auth/invitations/inspect", json={"token": invitation_token})
        await env.client.post(
            "/auth/invitations/accept", json={"token": invitation_token, "password": PASSWORD}
        )
        _remember_cookies(secrets, env.client)
        setup = (await env.client.post("/auth/mfa/setup", headers=csrf(env.client))).json()
        secrets.update({setup["secret"], setup["otpauth_uri"]})
        activate = await env.client.post(
            "/auth/mfa/activate",
            json={"code": env.clock.code(setup["secret"])},
            headers=csrf(env.client),
        )
        codes = activate.json()["recovery_codes"]
        secrets.update(codes)
        _remember_cookies(secrets, env.client)
        env.clock.next_step()
        await env.client.post("/auth/recovery-codes/ack", headers=csrf(env.client))
        _remember_cookies(secrets, env.client)

        # Failures, lockout counters and a recovery-code login.
        async with new_client(env.app) as other:
            for _ in range(2):
                await other.post("/auth/login", json={"email": email, "password": WRONG_PASSWORD})
            await other.post("/auth/login", json={"email": email, "password": PASSWORD})
            await other.post("/auth/mfa/verify", json={"code": "000000"}, headers=csrf(other))
            _remember_cookies(secrets, other)
            await other.post(
                "/auth/mfa/recover", json={"recovery_code": codes[0]}, headers=csrf(other)
            )
            _remember_cookies(secrets, other)

        # 422 on purpose: the rejected body contains the password.
        bad = await env.client.post(
            "/auth/login", json={"email": email, "password": PASSWORD, "extra": WRONG_PASSWORD}
        )
        assert bad.status_code == 422
        assert PASSWORD not in bad.text
        assert WRONG_PASSWORD not in bad.text

        # Password change and new recovery codes.
        env.clock.next_step()
        await env.client.post(
            "/account/password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "code": env.clock.code(setup["secret"]),
            },
            headers=csrf(env.client),
        )
        fresh = await env.client.post(
            "/account/recovery-codes", json={"password": NEW_PASSWORD}, headers=csrf(env.client)
        )
        secrets.update(fresh.json()["recovery_codes"])
        account = Account(user_id, tenant, email, PASSWORD, setup["secret"], codes)
        async with new_client(env.app) as third:
            env.clock.next_step()
            await login(third, account, env.clock, password=NEW_PASSWORD)
            _remember_cookies(secrets, third)

        # The pipeline itself must redact, whatever a future log call passes in.
        structlog.get_logger().info(
            "probe", password=PASSWORD, session_token="abc", nested={"recovery_code": codes[1]}
        )

        # What is stored is not what was typed.
        async with tenant_session(env.app.state.session_factory, tenant_id=tenant) as db:
            stored: str = (
                await db.execute(
                    text("SELECT password_hash FROM users WHERE id = :u"), {"u": user_id}
                )
            ).scalar_one()
        assert stored.startswith("$argon2id$")
        assert isinstance(user_id, uuid.UUID)

    out = capsys.readouterr()
    logged = "\n".join([out.out, out.err, caplog.text])

    assert "http_request" in logged, "the access log produced nothing: the check would be empty"
    assert logged.count("http_request") >= 12
    assert "[redacted]" in logged, "the redaction processor did not run"
    for secret in secrets:
        assert secret not in logged, f"a secret value leaked into the logs: {secret[:6]}..."
