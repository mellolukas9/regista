"""Machine access tokens, `KeyProvider.mac` and the `MachineRoute` marker (docs/adr/0018)."""

import base64
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.auth.machine import (
    MachineAuth,
    MachineRoute,
    MachineTokenError,
    MachineTokenExpired,
    issue_machine_token,
    parse_machine_token,
)
from regista_api.auth.sessions import SESSION_COOKIE
from regista_api.core.db import tenant_session
from regista_api.core.keys import LocalKeyProvider
from regista_api.main import create_app

from .conftest import DbUrls, Seed, api_client, make_settings

Factory = async_sessionmaker[AsyncSession]
LIFETIME = timedelta(minutes=15)


def _keys() -> LocalKeyProvider:
    return LocalKeyProvider(LocalKeyProvider.generate_key())


def _issue(keys: LocalKeyProvider, **overrides: object) -> str:
    values: dict[str, object] = {
        "machine_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "credential_version": 1,
        "lifetime": LIFETIME,
        **overrides,
    }
    token, _ = issue_machine_token(keys, **values)  # type: ignore[arg-type]
    return token


def _rebuild(token: str, payload_changes: dict[str, object]) -> str:
    """The token with edited claims and the *old* MAC (what an attacker could produce)."""
    head, body, mac = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload.update(payload_changes)
    new_body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{head}.{new_body}.{mac}"


# --- KeyProvider.mac --------------------------------------------------------------------------


def test_mac_is_deterministic_bound_to_purpose_and_to_the_master_key() -> None:
    keys = _keys()
    mac = keys.mac("agent-token", b"data")
    assert mac == keys.mac("agent-token", b"data")
    assert len(mac) == 32
    assert keys.verify_mac("agent-token", b"data", mac)
    assert not keys.verify_mac("agent-token", b"other", mac)
    assert not keys.verify_mac("other-purpose", b"data", mac)
    assert not keys.verify_mac("agent-token", b"data", mac[:-1] + bytes([mac[-1] ^ 1]))
    # Another master key derives another MAC key.
    assert not _keys().verify_mac("agent-token", b"data", mac)


# --- token format -----------------------------------------------------------------------------


def test_token_round_trip_and_shape() -> None:
    keys = _keys()
    machine_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token, expires_at = issue_machine_token(
        keys, machine_id=machine_id, tenant_id=tenant_id, credential_version=3, lifetime=LIFETIME
    )
    assert token.startswith("rga1.") and token.count(".") == 2
    claims = parse_machine_token(keys, token)
    assert (claims.machine_id, claims.tenant_id, claims.credential_version) == (
        machine_id,
        tenant_id,
        3,
    )
    assert abs(claims.expires_at - expires_at) < timedelta(seconds=1)
    assert abs(expires_at - (datetime.now(UTC) + LIFETIME)) < timedelta(seconds=5)


def test_tampered_or_foreign_tokens_are_rejected() -> None:
    keys = _keys()
    token = _issue(keys)
    target = uuid.uuid4()

    # Changing any claim without a new MAC (swapping the tenant, bumping the version, a later
    # expiry) fails: the MAC covers the whole payload.
    changes: list[dict[str, object]] = [
        {"tid": str(target)},
        {"cv": 99},
        {"exp": 4_000_000_000},
        {"mid": str(target)},
    ]
    for change in changes:
        with pytest.raises(MachineTokenError) as err:
            parse_machine_token(keys, _rebuild(token, change))
        assert not isinstance(err.value, MachineTokenExpired)

    with pytest.raises(MachineTokenError):
        parse_machine_token(_keys(), token)  # signed under another master key
    for bad in ("", "rga1", "rga1.x", "rga1.x.y.z", "rgb1." + token[5:], "x" * 2000):
        with pytest.raises(MachineTokenError):
            parse_machine_token(keys, bad)


def test_expired_token_is_distinguished_only_when_it_is_genuine() -> None:
    keys = _keys()
    past = datetime.now(UTC) - timedelta(hours=1)
    token = _issue(keys, now=past)
    with pytest.raises(MachineTokenExpired):
        parse_machine_token(keys, token)
    # A forged token cannot learn anything from the expired code: it is a plain error.
    with pytest.raises(MachineTokenError) as err:
        parse_machine_token(_keys(), token)
    assert not isinstance(err.value, MachineTokenExpired)
    # A user session token is not a machine token.
    with pytest.raises(MachineTokenError):
        parse_machine_token(keys, "some-opaque-session-token")


def test_a_payload_with_the_wrong_type_is_rejected_even_with_a_valid_mac() -> None:
    keys = _keys()
    body = base64.urlsafe_b64encode(
        json.dumps(
            {
                "typ": "user",
                "mid": str(uuid.uuid4()),
                "tid": str(uuid.uuid4()),
                "cv": 1,
                "exp": 4_000_000_000,
            }
        ).encode()
    ).rstrip(b"=")
    signed = f"rga1.{body.decode()}"
    mac = base64.urlsafe_b64encode(keys.mac("agent-token", signed.encode())).rstrip(b"=")
    with pytest.raises(MachineTokenError):
        parse_machine_token(keys, f"{signed}.{mac.decode()}")


# --- MachineRoute -----------------------------------------------------------------------------


@pytest_asyncio.fixture
async def machine_app(
    db_urls: DbUrls, seed: Seed
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app(make_settings(db_urls))

    @app.get("/agent/whoami")
    async def whoami(machine: MachineAuth = Depends(MachineRoute())) -> dict[str, str]:  # noqa: B008
        async with machine.session() as db:
            visible: int = (await db.execute(text("SELECT count(*) FROM machines"))).scalar_one()
        return {
            "machine_id": str(machine.machine_id),
            "tenant_id": str(machine.tenant_id),
            "mode": machine.mode,
            "visible_machines": str(visible),
        }

    async with api_client(app) as client:
        yield app, client


async def _enrolled_machine(
    factory: Factory, tenant_id: uuid.UUID, *, credential_version: int = 1
) -> uuid.UUID:
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        pool_id: uuid.UUID = (
            await db.execute(
                text("INSERT INTO pools (tenant_id, name) VALUES (:t, :n) RETURNING id"),
                {"t": tenant_id, "n": f"p-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
        machine_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name, public_key, status,"
                    " credential_version, mode, last_seen_at, enrolled_at)"
                    " VALUES (:t, :p, :n, :k, 'online', :cv, 'session', now(), now())"
                    " RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "p": pool_id,
                    "n": f"m-{uuid.uuid4().hex[:8]}",
                    "k": os.urandom(32),
                    "cv": credential_version,
                },
            )
        ).scalar_one()
    return machine_id


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_machine_route_accepts_a_valid_token_and_reads_under_rls(
    machine_app: tuple[FastAPI, httpx.AsyncClient], app_factory: Factory, seed: Seed
) -> None:
    app, client = machine_app
    mine = await _enrolled_machine(app_factory, seed.tenant_a)
    await _enrolled_machine(app_factory, seed.tenant_b)
    token = _issue(
        app.state.key_provider, machine_id=mine, tenant_id=seed.tenant_a, credential_version=1
    )

    response = await client.get("/agent/whoami", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["machine_id"] == str(mine) and body["tenant_id"] == str(seed.tenant_a)
    assert body["mode"] == "session"
    # RLS binds the request to the token's tenant: B's machine is not there.
    other_tenant_machines = await _count_in(app_factory, seed.tenant_a)
    assert int(body["visible_machines"]) == other_tenant_machines


async def _count_in(factory: Factory, tenant_id: uuid.UUID) -> int:
    async with tenant_session(factory, tenant_id=tenant_id) as db:
        count: int = (await db.execute(text("SELECT count(*) FROM machines"))).scalar_one()
    return count


async def test_machine_route_refuses_everything_that_is_not_a_good_token(
    machine_app: tuple[FastAPI, httpx.AsyncClient], app_factory: Factory, seed: Seed
) -> None:
    app, client = machine_app
    keys = app.state.key_provider
    mine = await _enrolled_machine(app_factory, seed.tenant_a)

    def good(**overrides: object) -> str:
        return _issue(keys, **{"machine_id": mine, "tenant_id": seed.tenant_a, **overrides})

    async def code(headers: dict[str, str]) -> tuple[int, str]:
        r = await client.get("/agent/whoami", headers=headers)
        return r.status_code, r.json()["detail"]["code"]

    assert await code({}) == (401, "not_authenticated")
    assert await code({"Authorization": "Basic abc"}) == (401, "not_authenticated")
    assert await code(_bearer("not-a-token")) == (401, "not_authenticated")
    assert await code(_bearer(good(now=datetime.now(UTC) - timedelta(hours=1)))) == (
        401,
        "token_expired",
    )
    # Signed by us, but for a machine that does not exist, or with another tenant's id.
    assert await code(_bearer(good(machine_id=uuid.uuid4()))) == (401, "not_authenticated")
    assert await code(_bearer(good(tenant_id=seed.tenant_b))) == (401, "not_authenticated")
    # The credential was replaced (re-enrollment bumped the version): the old token is dead.
    assert await code(_bearer(good(credential_version=0))) == (401, "not_authenticated")

    # A user session cookie is not a machine credential.
    client.cookies.set(SESSION_COOKIE, "whatever")
    assert await code({}) == (401, "not_authenticated")
    client.cookies.clear()


async def test_revocation_and_re_enrollment_take_effect_on_the_next_request(
    machine_app: tuple[FastAPI, httpx.AsyncClient], app_factory: Factory, seed: Seed
) -> None:
    app, client = machine_app
    keys = app.state.key_provider
    mine = await _enrolled_machine(app_factory, seed.tenant_a)
    token = _issue(keys, machine_id=mine, tenant_id=seed.tenant_a, credential_version=1)
    assert (await client.get("/agent/whoami", headers=_bearer(token))).status_code == 200

    # Re-enrollment: same machine, new credential version.
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as db:
        await db.execute(
            text("UPDATE machines SET credential_version = 2 WHERE id = :m"), {"m": mine}
        )
    stale = await client.get("/agent/whoami", headers=_bearer(token))
    assert (stale.status_code, stale.json()["detail"]["code"]) == (401, "not_authenticated")
    fresh = _issue(keys, machine_id=mine, tenant_id=seed.tenant_a, credential_version=2)
    assert (await client.get("/agent/whoami", headers=_bearer(fresh))).status_code == 200

    # Revocation: the very same valid token is refused with a distinct code.
    async with tenant_session(app_factory, tenant_id=seed.tenant_a) as db:
        await db.execute(
            text("UPDATE machines SET status = 'revoked', revoked_at = now() WHERE id = :m"),
            {"m": mine},
        )
    revoked = await client.get("/agent/whoami", headers=_bearer(fresh))
    assert (revoked.status_code, revoked.json()["detail"]["code"]) == (401, "machine_revoked")
