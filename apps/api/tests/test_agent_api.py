"""The agent protocol: enrollment, challenge-response login, access token and heartbeat."""

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_api.core.security import hash_token
from regista_api.machines.agent import audience, auth_message, enroll_message

from .agent_helpers import OS, AgentSim
from .agent_helpers import b64 as _b64
from .agent_helpers import fresh_ip as _fresh_ip
from .agent_helpers import raw_public as _raw_public
from .conftest import DbUrls, Seed, open_env
from .helpers import Env, csrf, new_client, onboard, unique_email

Factory = async_sessionmaker[AsyncSession]

VECTOR = json.loads(
    (Path(__file__).parents[3] / "docs" / "specs" / "agent-signing-vector.json").read_text("utf-8")
)
INVALID_KEY = (401, {"detail": {"code": "invalid_enrollment_key"}})
INVALID_CREDENTIALS = (401, {"detail": {"code": "invalid_credentials"}})


# --- the contract with the agent --------------------------------------------------------------


def test_the_signed_bytes_and_signatures_match_the_shared_vector() -> None:
    seed = bytes.fromhex(VECTOR["private_seed_hex"])
    private = Ed25519PrivateKey.from_private_bytes(seed)
    assert _b64(_raw_public(private)) == VECTOR["public_key_b64"]
    aud = audience(VECTOR["audience"] + "/")  # a trailing slash does not change the audience

    e = VECTOR["enroll"]
    key_hash = hash_token(e["key"])
    assert key_hash.hex() == e["key_hash_hex"]
    assert enroll_message(key_hash, aud).decode() == e["message"]
    assert _b64(private.sign(enroll_message(key_hash, aud))) == e["proof_b64"]

    a = VECTOR["auth"]
    message = auth_message(uuid.UUID(a["machine_id"]), a["nonce_b64"], aud)
    assert message.decode() == a["message"]
    assert _b64(private.sign(message)) == a["signature_b64"]


# --- fixtures and helpers ---------------------------------------------------------------------


@dataclass
class Ctx:
    env: Env
    seed: Seed
    admin_a: httpx.AsyncClient
    admin_b: httpx.AsyncClient
    agent: httpx.AsyncClient  # no cookies: the agent never has a session
    factory: Factory
    owner: Factory
    aud: str
    admin_a_id: uuid.UUID


@pytest_asyncio.fixture
async def ctx(db_urls: DbUrls, seed: Seed, owner_factory: Factory) -> AsyncIterator[Ctx]:
    async with open_env(db_urls) as env:
        admin_a, admin_b, agent = (new_client(env.app) for _ in range(3))
        a = await onboard(
            env.app,
            admin_a,
            seed.tenant_a,
            env.clock,
            role="tenant_admin",
            email=unique_email("agent-a"),
        )
        await onboard(
            env.app,
            admin_b,
            seed.tenant_b,
            env.clock,
            role="tenant_admin",
            email=unique_email("agent-b"),
        )
        try:
            yield Ctx(
                env=env,
                seed=seed,
                admin_a=admin_a,
                admin_b=admin_b,
                agent=agent,
                factory=env.app.state.session_factory,
                owner=owner_factory,
                aud=audience(env.app.state.settings.api_public_url),
                admin_a_id=a.user_id,
            )
        finally:
            for client in (admin_a, admin_b, agent):
                await client.aclose()


@dataclass
class Machine:
    id: str
    name: str
    key: str


async def _new_machine(c: Ctx, *, mode: str = "service") -> Machine:
    suffix = uuid.uuid4().hex[:8]
    pool = await c.admin_a.post("/pools", json={"name": f"pool-{suffix}"}, headers=csrf(c.admin_a))
    assert pool.status_code == 201, pool.text
    created = await c.admin_a.post(
        "/machines",
        json={"name": f"m-{suffix}", "pool_id": pool.json()["id"], "mode": mode},
        headers=csrf(c.admin_a),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    return Machine(body["machine_id"], body["name"], body["enrollment_key"])


async def _new_key(c: Ctx, machine: Machine) -> str:
    r = await c.admin_a.post(f"/machines/{machine.id}/enrollment-key", headers=csrf(c.admin_a))
    assert r.status_code == 201, r.text
    key: str = r.json()["enrollment_key"]
    return key


def make_agent(c: Ctx, private: Ed25519PrivateKey | None = None) -> AgentSim:
    return AgentSim(c.agent, c.aud, private)


async def _enrolled_agent(c: Ctx) -> tuple[Machine, AgentSim]:
    machine = await _new_machine(c)
    agent = make_agent(c)
    assert (await agent.enroll(machine.key)).status_code == 200
    await agent.login()
    return machine, agent


async def _owner(c: Ctx, sql: str, **params: Any) -> Any:
    async with tenant_session(c.owner, tenant_id=c.seed.tenant_a) as db:
        return (await db.execute(text(sql), params)).all()


async def _events(c: Ctx, machine_id: str) -> list[str]:
    rows = await _owner(
        c,
        "SELECT kind FROM machine_events WHERE machine_id = :m ORDER BY created_at, id",
        m=machine_id,
    )
    return [r[0] for r in rows]


# --- enrollment -------------------------------------------------------------------------------


async def test_enrollment_then_login_then_heartbeat(ctx: Ctx) -> None:
    machine = await _new_machine(ctx, mode="session")
    agent = make_agent(ctx)

    enrolled = await agent.enroll(machine.key)
    assert enrolled.status_code == 200, enrolled.text
    body = enrolled.json()
    # The client comes back so the agent can keep it and tell whose every package is (ADR 0021).
    assert body.pop("tenant_id") == str(ctx.seed.tenant_a)
    assert body == {"machine_id": machine.id, "mode": "session", "heartbeat_seconds": 30}
    assert enrolled.headers["cache-control"] == "no-store"

    row = (
        await _owner(
            ctx,
            "SELECT status, credential_version, public_key, enrolled_at, agent_version,"
            " os_info, last_seen_at FROM machines WHERE id = :m",
            m=machine.id,
        )
    )[0]
    assert row.status == "online" and row.credential_version == 1
    assert bytes(row.public_key) == _raw_public(agent.private)
    assert row.enrolled_at is not None and row.last_seen_at is not None
    assert row.agent_version == "0.1.0" and row.os_info == OS
    used = await _owner(
        ctx,
        "SELECT used_at, revoked_at FROM enrollment_keys WHERE machine_id = :m",
        m=machine.id,
    )
    assert used[0].used_at is not None and used[0].revoked_at is None
    assert await _events(ctx, machine.id) == ["enrolled"]

    audit = (
        await _owner(
            ctx,
            "SELECT actor_type, actor_id, action, metadata FROM audit_log"
            " WHERE target_id = :m AND action = 'machine.enrolled'",
            m=machine.id,
        )
    )[0]
    assert (str(audit.actor_id), audit.actor_type) == (machine.id, "machine")
    assert audit.metadata["key_created_by"] == str(ctx.admin_a_id)
    assert machine.key not in str(audit.metadata)

    token = await agent.login()
    assert token.startswith("rga1.")
    beat = await agent.heartbeat()
    assert beat.status_code == 200, beat.text
    body = beat.json()
    assert (body["heartbeat_seconds"], body["mode"], body["cancellations"]) == (30, "session", [])
    assert body["server_time"]
    await agent.heartbeat()
    assert await _events(ctx, machine.id) == ["enrolled", "first_signal"]

    # The panel sees it online, with the history.
    detail = (await ctx.admin_a.get(f"/machines/{machine.id}")).json()
    assert detail["status"] == "online" and detail["agent_version"] == "0.1.0"
    assert detail["os_info"] == OS and detail["key_expires_at"] is None


async def test_every_enrollment_failure_looks_the_same(ctx: Ctx) -> None:
    machine = await _new_machine(ctx)
    agent = make_agent(ctx)
    other = make_agent(ctx)

    expired = await _new_machine(ctx)
    await _owner_write(
        ctx,
        "UPDATE enrollment_keys SET expires_at = now() - interval '1 minute' WHERE machine_id = :m",
        m=expired.id,
    )
    replaced = await _new_machine(ctx)
    await _new_key(ctx, replaced)  # the first key of `replaced` is now revoked
    revoked_machine = await _new_machine(ctx)
    await ctx.admin_a.post(
        f"/machines/{revoked_machine.id}/revoke",
        json={"confirm_name": revoked_machine.name},
        headers=csrf(ctx.admin_a),
    )
    consumed = await _new_machine(ctx)
    assert (await make_agent(ctx).enroll(consumed.key)).status_code == 200

    good = agent.enroll_body(machine.key)
    cases: dict[str, dict[str, Any]] = {
        "unknown key": agent.enroll_body("rgk_nao-existe"),
        "expired key": make_agent(ctx).enroll_body(expired.key),
        "replaced (revoked) key": make_agent(ctx).enroll_body(replaced.key),
        "revoked machine": make_agent(ctx).enroll_body(revoked_machine.key),
        "key already used": make_agent(ctx).enroll_body(consumed.key),
        "proof by another key": {**good, "proof": other.enroll_body(machine.key)["proof"]},
        "proof for another audience": agent.enroll_body(machine.key, aud="https://outro.example"),
        "proof of another key hash": {**good, "proof": agent.enroll_body("rgk_outra")["proof"]},
        "public key not base64": {**good, "public_key": "###"},
        "public key too short": {**good, "public_key": _b64(b"x" * 31)},
        "proof too short": {**good, "proof": _b64(b"x" * 63)},
        "all zeros": {**good, "public_key": _b64(bytes(32)), "proof": _b64(bytes(64))},
    }
    seen: set[tuple[int, str]] = set()
    for label, body in cases.items():
        r = await ctx.agent.post("/agent/enroll", json=body, headers=_fresh_ip())
        assert (r.status_code, r.json()) == INVALID_KEY, f"{label}: {r.text}"
        seen.add((r.status_code, r.text))
    assert len(seen) == 1, "two failures answered differently"

    # None of that burned the live key: the right proof still enrolls.
    assert (await agent.enroll(machine.key)).status_code == 200


async def _owner_write(c: Ctx, sql: str, **params: Any) -> None:
    async with tenant_session(c.owner, tenant_id=c.seed.tenant_a) as db:
        await db.execute(text(sql), params)


async def test_two_simultaneous_enrollments_with_one_key_only_one_wins(ctx: Ctx) -> None:
    machine = await _new_machine(ctx)
    agents = [make_agent(ctx) for _ in range(5)]
    results = await asyncio.gather(*(a.enroll(machine.key) for a in agents))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 401, 401, 401, 401], codes

    winner = agents[[r.status_code for r in results].index(200)]
    row = (
        await _owner(
            ctx, "SELECT credential_version, public_key FROM machines WHERE id = :m", m=machine.id
        )
    )[0]
    assert row.credential_version == 1
    assert bytes(row.public_key) == _raw_public(winner.private)
    assert await _events(ctx, machine.id) == ["enrolled"]


async def test_enrolling_again_replaces_the_credential_and_kills_the_old_agent(
    ctx: Ctx,
) -> None:
    machine, old = await _enrolled_agent(ctx)
    old_token = old.token
    assert (await old.heartbeat()).status_code == 200

    new = make_agent(ctx)
    new_key = await _new_key(ctx, machine)
    # Nothing changes until the new key is used: the old agent keeps working.
    assert (await old.heartbeat()).status_code == 200
    assert (await new.enroll(new_key)).status_code == 200

    stale = await old.heartbeat(old_token)
    assert (stale.status_code, stale.json()["detail"]["code"]) == (401, "not_authenticated")
    # The old key pair cannot get a new token either: the public key on file is the new one.
    challenge = await old.challenge()
    nonce = challenge.json()["nonce"]
    refused = await old.exchange(nonce, old.sign(nonce))
    assert (refused.status_code, refused.json()) == INVALID_CREDENTIALS

    await new.login()
    assert (await new.heartbeat()).status_code == 200
    row = (
        await _owner(ctx, "SELECT credential_version FROM machines WHERE id = :m", m=machine.id)
    )[0]
    assert row.credential_version == 2
    # The new agent's first heartbeat is a first signal again: a new credential, a fresh start.
    assert await _events(ctx, machine.id) == [
        "enrolled",
        "first_signal",
        "re_enrolled",
        "first_signal",
    ]
    actions = [
        r[0]
        for r in await _owner(
            ctx,
            "SELECT action FROM audit_log WHERE target_id = :m ORDER BY created_at, id",
            m=machine.id,
        )
    ]
    assert actions == [
        "machine.created",
        "machine.enrolled",
        "machine.key_generated",
        "machine.re_enrolled",
    ]


# --- challenge and token ----------------------------------------------------------------------


async def test_a_machine_that_cannot_log_in_gets_the_same_answer(ctx: Ctx) -> None:
    pending = await _new_machine(ctx)
    revoked, agent = await _enrolled_agent(ctx)
    await ctx.admin_a.post(
        f"/machines/{revoked.id}/revoke",
        json={"confirm_name": revoked.name},
        headers=csrf(ctx.admin_a),
    )
    for label, machine_id in {
        "unknown": str(uuid.uuid4()),
        "pending": pending.id,
        "revoked": revoked.id,
    }.items():
        r = await agent.challenge(machine_id)
        assert (r.status_code, r.json()) == INVALID_CREDENTIALS, label
        r = await agent.exchange(_b64(bytes(32)), _b64(bytes(64)), machine_id)
        assert (r.status_code, r.json()) == INVALID_CREDENTIALS, label


async def test_the_nonce_is_single_use_short_lived_and_replaces_the_previous_one(
    ctx: Ctx,
) -> None:
    machine, agent = await _enrolled_agent(ctx)

    first = (await agent.challenge()).json()
    assert first["expires_in"] == 60 and len(base64.b64decode(first["nonce"])) == 32
    signature = agent.sign(first["nonce"])
    assert (await agent.exchange(first["nonce"], signature)).status_code == 200
    replay = await agent.exchange(first["nonce"], signature)
    assert (replay.status_code, replay.json()) == INVALID_CREDENTIALS

    # A newer challenge invalidates the older one.
    older = (await agent.challenge()).json()["nonce"]
    newer = (await agent.challenge()).json()["nonce"]
    assert older != newer
    refused = await agent.exchange(older, agent.sign(older))
    assert (refused.status_code, refused.json()) == INVALID_CREDENTIALS
    assert (await agent.exchange(newer, agent.sign(newer))).status_code == 200

    # Expired nonce.
    nonce = (await agent.challenge()).json()["nonce"]
    await _owner_write(
        ctx,
        "UPDATE machines SET challenge_expires_at = now() - interval '1 second' WHERE id = :m",
        m=machine.id,
    )
    late = await agent.exchange(nonce, agent.sign(nonce))
    assert (late.status_code, late.json()) == INVALID_CREDENTIALS


async def test_a_bad_signature_is_refused_and_does_not_burn_the_nonce(ctx: Ctx) -> None:
    machine, agent = await _enrolled_agent(ctx)
    stranger = make_agent(ctx)
    nonce = (await agent.challenge()).json()["nonce"]

    bad = {
        "signed by another key": stranger.sign(nonce, machine_id=machine.id),
        "signed for another audience": agent.sign(nonce, aud="https://outro.example"),
        "signed another nonce": agent.sign(_b64(bytes(32))),
        "signed for another machine": agent.sign(nonce, machine_id=str(uuid.uuid4())),
        "not base64": "###",
        "too short": _b64(b"x" * 63),
    }
    for label, signature in bad.items():
        r = await agent.exchange(nonce, signature)
        assert (r.status_code, r.json()) == INVALID_CREDENTIALS, label

    # Someone who only knows the machine id could not use up the real agent's challenge.
    assert (await agent.exchange(nonce, agent.sign(nonce))).status_code == 200


async def test_a_nonce_of_one_machine_does_not_work_for_another(ctx: Ctx) -> None:
    _, one = await _enrolled_agent(ctx)
    _, two = await _enrolled_agent(ctx)
    nonce = (await one.challenge()).json()["nonce"]
    # `two` has no challenge of its own: its signature over one's nonce does not match anything.
    r = await two.exchange(nonce, two.sign(nonce))
    assert (r.status_code, r.json()) == INVALID_CREDENTIALS
    # `one`'s key signing for `two`'s id is checked against two's public key.
    r = await two.exchange(nonce, one.sign(nonce, machine_id=two.machine_id))
    assert (r.status_code, r.json()) == INVALID_CREDENTIALS
    # None of that touched one's challenge: the legitimate pair still gets its token.
    assert (await one.exchange(nonce, one.sign(nonce))).status_code == 200


# --- heartbeat --------------------------------------------------------------------------------


async def test_heartbeat_records_the_history_and_keeps_only_the_known_fields(ctx: Ctx) -> None:
    machine, agent = await _enrolled_agent(ctx)
    assert (await agent.heartbeat()).status_code == 200  # first_signal

    await _owner_write(ctx, "UPDATE machines SET status = 'offline' WHERE id = :m", m=machine.id)
    back = await agent.heartbeat(
        agent_version="0.2.0", os_info={**OS, "evil": "<script>alert(1)</script>"}
    )
    assert back.status_code == 200, back.text
    assert await _events(ctx, machine.id) == [
        "enrolled",
        "first_signal",
        "came_back",
        "agent_updated",
    ]
    row = (
        await _owner(
            ctx,
            "SELECT status, agent_version, os_info FROM machines WHERE id = :m",
            m=machine.id,
        )
    )[0]
    assert (row.status, row.agent_version) == ("online", "0.2.0")
    assert row.os_info == OS  # the unknown key never reached the database
    updated = (
        await _owner(
            ctx,
            "SELECT metadata FROM machine_events WHERE machine_id = :m AND kind = 'agent_updated'",
            m=machine.id,
        )
    )[0]
    assert updated.metadata == {"from": "0.1.0", "to": "0.2.0"}


async def test_heartbeat_input_is_bounded_and_strict(ctx: Ctx) -> None:
    _, agent = await _enrolled_agent(ctx)
    for body in (
        {"agent_version": "x" * 33, "os_info": OS},
        {"agent_version": "", "os_info": OS},
        {"agent_version": "0.1.0", "os_info": {"system": "x" * 101}},
        {"agent_version": "0.1.0", "os_info": OS, "command": "rm -rf /"},
        {"agent_version": "0.1.0"},
    ):
        r = await ctx.agent.post(
            "/agent/heartbeat", json=body, headers={"Authorization": f"Bearer {agent.token}"}
        )
        assert r.status_code == 422, body
        assert "rm -rf" not in r.text  # the rejected input is not echoed back


async def test_a_revoked_machine_is_refused_on_its_very_next_request(ctx: Ctx) -> None:
    machine, agent = await _enrolled_agent(ctx)
    assert (await agent.heartbeat()).status_code == 200
    await ctx.admin_a.post(
        f"/machines/{machine.id}/revoke",
        json={"confirm_name": machine.name},
        headers=csrf(ctx.admin_a),
    )
    r = await agent.heartbeat()
    assert (r.status_code, r.json()["detail"]["code"]) == (401, "machine_revoked")
    # And it cannot get a new token (it gets the generic answer, learning nothing more).
    challenge = await agent.challenge()
    assert (challenge.status_code, challenge.json()) == INVALID_CREDENTIALS


async def test_a_user_session_is_not_a_machine_credential(ctx: Ctx) -> None:
    _, agent = await _enrolled_agent(ctx)
    # The admin's browser (cookies + CSRF) on a machine route, and the machine token on user routes.
    r = await ctx.admin_a.post(
        "/agent/heartbeat",
        json={"agent_version": "0.1.0", "os_info": OS},
        headers=csrf(ctx.admin_a),
    )
    assert r.status_code == 401
    for path in ("/machines", "/pools", "/auth/me", "/users"):
        r = await ctx.agent.get(path, headers={"Authorization": f"Bearer {agent.token}"})
        assert r.status_code == 401, path


# --- abuse limits -----------------------------------------------------------------------------


async def test_enrollment_and_login_are_rate_limited(db_urls: DbUrls, seed: Seed) -> None:
    async with open_env(
        db_urls,
        rate_agent_enroll_ip_per_minute=3,
        rate_agent_auth_machine_per_minute=3,
    ) as env:
        client = new_client(env.app)
        ip = _fresh_ip()
        body = {
            "key": "rgk_x",
            "public_key": _b64(bytes(32)),
            "proof": _b64(bytes(64)),
            "agent_version": "0.1.0",
            "os_info": OS,
        }
        codes = [
            (await client.post("/agent/enroll", json=body, headers=ip)).status_code
            for _ in range(5)
        ]
        assert codes == [401, 401, 401, 429, 429]

        machine_id = str(uuid.uuid4())
        codes = [
            (
                await client.post(
                    "/agent/challenge", json={"machine_id": machine_id}, headers=_fresh_ip()
                )
            ).status_code
            for _ in range(5)
        ]
        assert codes == [401, 401, 401, 429, 429]
        await client.aclose()


async def test_oversized_bodies_are_refused_unread(ctx: Ctx) -> None:
    big = {"key": "x" * 20_000}
    declared = await ctx.agent.post("/agent/enroll", json=big, headers=_fresh_ip())
    assert declared.status_code == 413

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(40):
            yield b" " * 1024  # no Content-Length: it is only found out while streaming

    streamed = await ctx.agent.post(
        "/agent/enroll",
        content=chunks(),
        headers={**_fresh_ip(), "Content-Type": "application/json"},
    )
    assert streamed.status_code == 413, streamed.text
    # The app keeps serving: a small, incomplete body is judged normally.
    ok = await ctx.agent.post("/agent/enroll", json={"key": "x"}, headers=_fresh_ip())
    assert ok.status_code == 422


async def test_public_agent_routes_only_take_json(ctx: Ctx) -> None:
    for path in ("/agent/enroll", "/agent/challenge", "/agent/token"):
        r = await ctx.agent.post(
            path, content="key=a", headers={**_fresh_ip(), "Content-Type": "text/plain"}
        )
        assert r.status_code == 415, path


@pytest.mark.parametrize(
    "path", ["/agent/enroll", "/agent/challenge", "/agent/token", "/agent/heartbeat"]
)
async def test_agent_responses_are_never_cached(ctx: Ctx, path: str) -> None:
    r = await ctx.agent.post(path, json={}, headers=_fresh_ip())
    assert r.headers["cache-control"] == "no-store"
