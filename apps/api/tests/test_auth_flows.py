"""End-to-end auth flows over HTTP (invitation, password, TOTP, recovery codes, sessions)."""

import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session

from .conftest import DbUrls, open_env
from .helpers import (
    NEW_PASSWORD,
    PASSWORD,
    Account,
    Env,
    csrf,
    invite_user,
    login,
    new_client,
    onboard,
    unique_email,
)

Factory = async_sessionmaker[AsyncSession]
RECOVERY_RE = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")


async def _scalar(env: Env, tenant_id: uuid.UUID, sql: str, **params: object) -> object:
    async with tenant_session(env.app.state.session_factory, tenant_id=tenant_id) as db:
        return (await db.execute(text(sql), params)).scalar_one()


async def _exec(env: Env, tenant_id: uuid.UUID, sql: str, **params: object) -> None:
    async with tenant_session(env.app.state.session_factory, tenant_id=tenant_id) as db:
        await db.execute(text(sql), params)


async def _audit_actions(
    owner_factory: Factory, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[str]:
    # regista_app cannot read audit_log (append-only); the owner can, still under RLS.
    async with tenant_session(owner_factory, tenant_id=tenant_id) as db:
        rows = await db.execute(
            text("SELECT action FROM audit_log WHERE actor_id = :u ORDER BY created_at"),
            {"u": user_id},
        )
        return [r[0] for r in rows]


# --- onboarding -------------------------------------------------------------------------------


async def test_invitation_to_active_session(env: Env, seed: object, owner_factory: Factory) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    user_id, email, token = await invite_user(env.app, tenant, role="operator")

    inspect = await env.client.post("/auth/invitations/inspect", json={"token": token})
    assert inspect.status_code == 200
    assert inspect.json() == {"email": email}

    accept = await env.client.post(
        "/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert accept.status_code == 200
    assert accept.json() == {"stage": "mfa_setup"}
    cookies = accept.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("regista_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("regista_csrf="))
    assert "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()  # the panel reads it to echo the header

    me = await env.client.get("/auth/me")
    assert me.json() == {
        "stage": "mfa_setup",
        "email": email,
        "role": None,
        "display_name": None,
        "is_platform_admin": False,
        "tenant_id": None,
        "tenant_name": None,
        "mfa_enabled": False,
        "mfa_enabled_at": None,
        "recovery_codes_remaining": None,
    }

    setup = await env.client.post("/auth/mfa/setup", headers=csrf(env.client))
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert email.replace("@", "%40") in setup.json()["otpauth_uri"]

    activate = await env.client.post(
        "/auth/mfa/activate", json={"code": env.clock.code(secret)}, headers=csrf(env.client)
    )
    assert activate.status_code == 200
    codes = activate.json()["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(RECOVERY_RE.match(c) for c in codes)
    assert (await env.client.get("/auth/me")).json()["stage"] == "recovery_codes"

    env.clock.next_step()
    ack = await env.client.post("/auth/recovery-codes/ack", headers=csrf(env.client))
    assert ack.json() == {"stage": "active"}

    me = (await env.client.get("/auth/me")).json()
    assert me["stage"] == "active"
    assert me["email"] == email
    assert me["role"] == "operator"
    assert me["tenant_id"] == str(tenant)
    assert me["mfa_enabled"] is True
    assert me["mfa_enabled_at"] is not None
    assert me["recovery_codes_remaining"] == 10

    status = await _scalar(env, tenant, "SELECT status FROM users WHERE id = :u", u=user_id)
    assert status == "active"
    assert await _audit_actions(owner_factory, tenant, user_id) == [
        "user.invitation_accepted",
        "auth.mfa_enabled",
        "auth.login",
    ]


async def test_secrets_are_encrypted_and_hashed_at_rest(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    row = await _scalar(
        env,
        tenant,
        "SELECT concat_ws('|', password_hash, encode(mfa_secret_enc, 'escape'))"
        " FROM users WHERE id = :u",
        u=account.user_id,
    )
    assert isinstance(row, str)
    assert account.totp_secret not in row
    assert PASSWORD not in row
    assert row.startswith("$argon2id$")
    hashes = await _scalar(
        env,
        tenant,
        "SELECT string_agg(code_hash, ',') FROM recovery_codes WHERE user_id = :u",
        u=account.user_id,
    )
    assert isinstance(hashes, str)
    assert not any(code in hashes for code in account.recovery_codes)


async def test_session_token_changes_at_every_stage(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    _, _, token = await invite_user(env.app, tenant)
    seen = []

    await env.client.post("/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    seen.append(env.client.cookies["regista_session"])
    setup = await env.client.post("/auth/mfa/setup", headers=csrf(env.client))
    assert env.client.cookies["regista_session"] == seen[-1]  # setup is not a stage change
    activate = await env.client.post(
        "/auth/mfa/activate",
        json={"code": env.clock.code(setup.json()["secret"])},
        headers=csrf(env.client),
    )
    assert activate.status_code == 200
    seen.append(env.client.cookies["regista_session"])
    env.clock.next_step()
    await env.client.post("/auth/recovery-codes/ack", headers=csrf(env.client))
    seen.append(env.client.cookies["regista_session"])
    assert len(set(seen)) == 3


async def test_invitation_is_single_use_and_replaced_by_a_newer_one(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    user_id, _email, old_token = await invite_user(env.app, tenant)

    # A newer invitation for the same user revokes the older link.
    from regista_api.auth.invitations import issue_invitation

    async with tenant_session(env.app.state.session_factory, tenant_id=tenant) as db:
        new_token = await issue_invitation(
            db, tenant_id=tenant, user_id=user_id, created_by=None, settings=env.app.state.settings
        )
    stale = await env.client.post("/auth/invitations/inspect", json={"token": old_token})
    assert stale.status_code == 404
    assert stale.json()["detail"] == {"code": "invitation_invalid"}

    first = await env.client.post(
        "/auth/invitations/accept", json={"token": new_token, "password": PASSWORD}
    )
    assert first.status_code == 200
    other = new_client(env.app)
    async with other:
        again = await other.post(
            "/auth/invitations/accept", json={"token": new_token, "password": PASSWORD}
        )
        gone = await other.post("/auth/invitations/inspect", json={"token": new_token})
    assert again.status_code == 404
    assert gone.status_code == 404


async def test_invitation_expired_and_unknown(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    user_id, _, token = await invite_user(env.app, tenant)
    await _exec(
        env,
        tenant,
        "UPDATE invitations SET expires_at = now() - interval '1 minute' WHERE user_id = :u",
        u=user_id,
    )
    for body in ({"token": token}, {"token": "nao-existe"}):
        r = await env.client.post("/auth/invitations/inspect", json=body)
        assert r.status_code == 404
    r = await env.client.post(
        "/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 404
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("curta-1234", "too_short"),
        ("password1234", "too_common"),
        ("Password123456", "too_common"),
    ],
)
async def test_weak_passwords_are_rejected_without_burning_the_invitation(
    env: Env, seed: object, password: str, code: str
) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    _, _, token = await invite_user(env.app, tenant)
    bad = await env.client.post(
        "/auth/invitations/accept", json={"token": token, "password": password}
    )
    assert bad.status_code == 422
    assert bad.json()["detail"] == {"code": code}
    ok = await env.client.post(
        "/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert ok.status_code == 200


async def test_public_post_routes_require_json(env: Env) -> None:
    for path in ("/auth/login", "/auth/invitations/inspect", "/auth/invitations/accept"):
        r = await env.client.post(path, data={"email": "a@example.com", "password": "x"})
        assert r.status_code == 415, path


# --- login ------------------------------------------------------------------------------------


async def test_login_with_totp_and_replay_protection(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)

    async with new_client(env.app) as browser:
        first = await browser.post(
            "/auth/login", json={"email": account.email.upper(), "password": PASSWORD}
        )
        assert first.json() == {"stage": "mfa_required"}
        assert (await browser.get("/auth/me")).json()["stage"] == "mfa_required"

        wrong = await browser.post(
            "/auth/mfa/verify", json={"code": "000000"}, headers=csrf(browser)
        )
        assert wrong.status_code == 401
        assert wrong.json()["detail"] == {"code": "invalid_code"}

        # The step used while onboarding is already spent: replaying it must fail.
        env.clock.now -= 30
        replay = await browser.post(
            "/auth/mfa/verify",
            json={"code": env.clock.code(account.totp_secret)},
            headers=csrf(browser),
        )
        assert replay.status_code == 401
        env.clock.now += 60

        good = await browser.post(
            "/auth/mfa/verify",
            json={"code": env.clock.code(account.totp_secret)},
            headers=csrf(browser),
        )
        assert good.json() == {"stage": "active"}
        assert (await browser.get("/auth/me")).json()["stage"] == "active"


async def test_login_with_recovery_code_is_single_use(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    code = account.recovery_codes[0]

    async with new_client(env.app) as browser:
        await browser.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        ok = await browser.post(
            "/auth/mfa/recover", json={"recovery_code": code.lower()}, headers=csrf(browser)
        )
        assert ok.json() == {"stage": "active"}
        me = (await browser.get("/auth/me")).json()
        assert me["recovery_codes_remaining"] == 9

    async with new_client(env.app) as browser:
        await browser.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        reused = await browser.post(
            "/auth/mfa/recover", json={"recovery_code": code}, headers=csrf(browser)
        )
        assert reused.status_code == 401
        assert reused.json()["detail"] == {"code": "invalid_recovery_code"}


async def test_login_failures_look_the_same(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    user_id, pending_email, _ = await invite_user(env.app, tenant)  # invited, no password
    disabled = await onboard(env.app, new_client(env.app), tenant, env.clock)
    await _exec(
        env, tenant, "UPDATE users SET status = 'disabled' WHERE id = :u", u=disabled.user_id
    )

    attempts = [
        (account.email, "senha-errada-123456"),
        (unique_email("ghost"), "senha-errada-123456"),
        (pending_email, PASSWORD),
        (disabled.email, PASSWORD),
    ]
    bodies = set()
    for email, password in attempts:
        r = await env.client.post("/auth/login", json={"email": email, "password": password})
        assert r.status_code == 401, email
        assert "set-cookie" not in r.headers
        bodies.add(r.text)
    assert len(bodies) == 1
    assert user_id is not None


async def test_lockout_is_progressive_and_clears_after_success(
    env: Env, seed: object, owner_factory: Factory
) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    settings = env.app.state.settings

    async def fail() -> int:
        r = await env.client.post(
            "/auth/login", json={"email": account.email, "password": "errada-errada-123"}
        )
        return r.status_code

    async def lock_seconds() -> float:
        value = await _scalar(
            env,
            tenant,
            "SELECT extract(epoch FROM locked_until - now()) FROM users WHERE id = :u",
            u=account.user_id,
        )
        return float(value)  # type: ignore[arg-type]

    for _ in range(settings.lockout_threshold - 1):
        assert await fail() == 401
    assert await fail() == 401  # the failure that triggers the lock still answers 401
    first_lock = await lock_seconds()
    assert 50 < first_lock <= settings.lockout_base_seconds

    # Locked: even the right password is refused, and the answer is the rate-limit one.
    locked = await env.client.post(
        "/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert locked.status_code == 429
    assert locked.json()["detail"] == {"code": "rate_limited"}

    # Lock expires; one more failure doubles it.
    await _exec(
        env,
        tenant,
        "UPDATE users SET locked_until = now() - interval '1 s' WHERE id = :u",
        u=account.user_id,
    )
    assert await fail() == 401
    second_lock = await lock_seconds()
    assert 110 < second_lock <= 2 * settings.lockout_base_seconds

    # After expiry the right password works, and finishing the login forgives the failures.
    await _exec(
        env,
        tenant,
        "UPDATE users SET locked_until = now() - interval '1 s' WHERE id = :u",
        u=account.user_id,
    )
    async with new_client(env.app) as browser:
        env.clock.next_step()
        done = await login(browser, account, env.clock)
        assert done.json() == {"stage": "active"}
    assert (
        await _scalar(
            env, tenant, "SELECT failed_logins FROM users WHERE id = :u", u=account.user_id
        )
        == 0
    )

    actions = await _audit_actions(owner_factory, tenant, account.user_id)
    assert actions.count("auth.login_failed") == settings.lockout_threshold + 1


async def test_wrong_totp_codes_count_towards_the_same_lockout(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    async with new_client(env.app) as browser:
        await browser.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        for _ in range(env.app.state.settings.lockout_threshold):
            r = await browser.post(
                "/auth/mfa/verify", json={"code": "111111"}, headers=csrf(browser)
            )
            assert r.status_code == 401
        blocked = await browser.post(
            "/auth/mfa/verify",
            json={"code": env.clock.code(account.totp_secret)},
            headers=csrf(browser),
        )
        assert blocked.status_code == 429


# --- rate limits ------------------------------------------------------------------------------


async def test_login_rate_limit_per_email(db_urls: DbUrls, seed: object) -> None:
    async with open_env(db_urls, rate_login_email_per_15min=3) as env:
        email = unique_email("limited")
        codes = [
            (
                await env.client.post("/auth/login", json={"email": email, "password": "x" * 14})
            ).status_code
            for _ in range(5)
        ]
        assert codes == [401, 401, 401, 429, 429]
        other = await env.client.post(
            "/auth/login", json={"email": unique_email(), "password": "x" * 14}
        )
        assert other.status_code == 401


async def test_login_rate_limit_per_ip_uses_the_forwarded_address(
    db_urls: DbUrls, seed: object
) -> None:
    async with open_env(db_urls, rate_login_ip_per_minute=2) as env:
        ip = f"198.51.100.{uuid.uuid4().int % 200 + 1}"
        headers = {"X-Forwarded-For": ip}
        codes = [
            (
                await env.client.post(
                    "/auth/login",
                    json={"email": unique_email(), "password": "x" * 14},
                    headers=headers,
                )
            ).status_code
            for _ in range(3)
        ]
        assert codes == [401, 401, 429]
        elsewhere = await env.client.post(
            "/auth/login",
            json={"email": unique_email(), "password": "x" * 14},
            headers={"X-Forwarded-For": "203.0.113.77"},
        )
        assert elsewhere.status_code == 401


async def test_mfa_attempts_are_rate_limited_per_session(db_urls: DbUrls, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    async with open_env(db_urls, rate_mfa_per_5min=2, lockout_threshold=50) as env:
        account = await onboard(env.app, env.client, tenant, env.clock)
        async with new_client(env.app) as browser:
            await browser.post("/auth/login", json={"email": account.email, "password": PASSWORD})
            codes = [
                (
                    await browser.post(
                        "/auth/mfa/verify", json={"code": "222222"}, headers=csrf(browser)
                    )
                ).status_code
                for _ in range(3)
            ]
        assert codes == [401, 401, 429]


# --- sessions, CSRF, stages -------------------------------------------------------------------


async def test_csrf_header_is_required_and_checked(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    async with new_client(env.app) as browser:
        await browser.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        body = {"code": env.clock.code(account.totp_secret)}
        missing = await browser.post("/auth/mfa/verify", json=body)
        wrong = await browser.post(
            "/auth/mfa/verify", json=body, headers={"X-CSRF-Token": "nao-e-este"}
        )
        assert missing.status_code == wrong.status_code == 403
        assert missing.json()["detail"] == {"code": "csrf_invalid"}
        ok = await browser.post("/auth/mfa/verify", json=body, headers=csrf(browser))
        assert ok.status_code == 200


async def test_partial_session_cannot_use_routes_of_other_stages(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    _, _, token = await invite_user(env.app, tenant)
    await env.client.post("/auth/invitations/accept", json={"token": token, "password": PASSWORD})
    # In stage mfa_setup: no verify, no recover, no recovery-code routes.
    for path, body in (
        ("/auth/mfa/verify", {"code": "123456"}),
        ("/auth/mfa/recover", {"recovery_code": "ABCD-2345"}),
    ):
        r = await env.client.post(path, json=body, headers=csrf(env.client))
        assert r.status_code == 403, path
        assert r.json()["detail"] == {"code": "session_stage", "stage": "mfa_setup"}
    for path in ("/auth/recovery-codes/reissue", "/auth/recovery-codes/ack"):
        r = await env.client.post(path, headers=csrf(env.client))
        assert r.status_code == 403, path


async def test_unauthenticated_and_forged_cookies_are_rejected(env: Env) -> None:
    assert (await env.client.get("/auth/me")).status_code == 401
    env.client.cookies.set("regista_session", "valor-forjado")
    r = await env.client.get("/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "not_authenticated"}


async def test_session_expiry_idle_absolute_and_partial(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    assert (await env.client.get("/auth/me")).status_code == 200

    await _exec(
        env,
        tenant,
        "UPDATE sessions SET last_seen_at = now() - interval '13 hours' WHERE user_id = :u",
        u=account.user_id,
    )
    assert (await env.client.get("/auth/me")).status_code == 401

    async with new_client(env.app) as browser:
        env.clock.next_step()
        await login(browser, account, env.clock)
        assert (await browser.get("/auth/me")).status_code == 200
        await _exec(
            env,
            tenant,
            "UPDATE sessions SET expires_at = now() - interval '1 s' WHERE user_id = :u",
            u=account.user_id,
        )
        assert (await browser.get("/auth/me")).status_code == 401

    async with new_client(env.app) as browser:
        await browser.post("/auth/login", json={"email": account.email, "password": PASSWORD})
        assert (await browser.get("/auth/me")).status_code == 200
        await _exec(
            env,
            tenant,
            "UPDATE sessions SET expires_at = now() - interval '1 s' WHERE user_id = :u",
            u=account.user_id,
        )
        assert (await browser.get("/auth/me")).status_code == 401


async def test_disabling_a_user_ends_the_session_on_the_next_request(
    env: Env, seed: object
) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    assert (await env.client.get("/auth/me")).status_code == 200
    await _exec(
        env, tenant, "UPDATE users SET status = 'disabled' WHERE id = :u", u=account.user_id
    )
    assert (await env.client.get("/auth/me")).status_code == 401


async def test_logout_revokes_the_session_and_clears_cookies(
    env: Env, seed: object, owner_factory: Factory
) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    account = await onboard(env.app, env.client, tenant, env.clock)
    old_cookie = env.client.cookies["regista_session"]
    out = await env.client.post("/auth/logout", headers=csrf(env.client))
    assert out.status_code == 204
    assert any(
        "regista_session=" in c and "max-age=0" in c.lower()
        for c in out.headers.get_list("set-cookie")
    )

    env.client.cookies.set("regista_session", old_cookie)
    assert (await env.client.get("/auth/me")).status_code == 401
    assert "auth.logout" in await _audit_actions(owner_factory, tenant, account.user_id)


async def test_auth_responses_are_not_cacheable(env: Env, seed: object) -> None:
    tenant = seed.tenant_a  # type: ignore[attr-defined]
    _, email, token = await invite_user(env.app, tenant)
    responses = [
        await env.client.post("/auth/invitations/inspect", json={"token": token}),
        await env.client.post(
            "/auth/invitations/accept", json={"token": token, "password": PASSWORD}
        ),
        await env.client.get("/auth/me"),
    ]
    setup = await env.client.post("/auth/mfa/setup", headers=csrf(env.client))
    responses.append(setup)  # carries the TOTP secret and the otpauth:// URI
    activate = await env.client.post(
        "/auth/mfa/activate",
        json={"code": env.clock.code(setup.json()["secret"])},
        headers=csrf(env.client),
    )
    responses.append(activate)  # carries the recovery codes
    responses.append(
        await env.client.post("/auth/recovery-codes/reissue", headers=csrf(env.client))
    )
    responses.append(
        await env.client.post("/auth/login", json={"email": email, "password": "x" * 14})
    )
    for r in responses:
        assert r.headers["cache-control"] == "no-store", r.request.url
        assert r.headers["x-content-type-options"] == "nosniff"


async def test_clients_of_other_tenants_cannot_use_my_invitation_token(
    env: Env, seed: object
) -> None:
    # The token alone identifies the invitation: it is bound to its own tenant's user.
    tenant_a, tenant_b = seed.tenant_a, seed.tenant_b  # type: ignore[attr-defined]
    _, email_a, token_a = await invite_user(env.app, tenant_a)
    await onboard(env.app, new_client(env.app), tenant_b, env.clock)
    inspect = await env.client.post("/auth/invitations/inspect", json={"token": token_a})
    assert inspect.json() == {"email": email_a}


def test_account_dataclass_defaults() -> None:
    account = Account(uuid.uuid4(), uuid.uuid4(), "a@example.com")
    assert account.password == PASSWORD
    assert NEW_PASSWORD != PASSWORD
