"""What the agent calls: enrollment, challenge-response login and heartbeat (docs/adr/0018).

- `POST /agent/enroll`: a single-use key plus a proof of possession of the new public key.
- `POST /agent/challenge` + `POST /agent/token`: the agent signs a one-time nonce with the private
  key that never left its machine and gets a 15-minute access token (`auth/machine.py`).
- `POST /agent/heartbeat`: "I am alive" every `heartbeat_seconds`, with the history events.

Everything the agent sends is untrusted data: fields are size-limited by a fixed schema. Every
failure on the three public routes answers the same 401, so nothing tells a caller whether a key,
a machine id or a signature was the wrong part. The agent only ever makes outgoing HTTPS calls.
"""

import base64
import binascii
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.config import JsonDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.deps import AppState, PublicRoute, get_state
from regista_api.auth.flow import limit
from regista_api.auth.machine import MachineAuth, MachineRoute, issue_machine_token
from regista_api.auth.rate_limit import client_ip
from regista_api.core.db import tenant_session
from regista_api.core.errors import api_error
from regista_api.core.security import hash_token
from regista_api.jobs import service as job_service
from regista_api.machines import service
from regista_api.machines.ed25519 import is_weak_public_key

router = APIRouter(prefix="/agent", tags=["agent"])

CHALLENGE_SECONDS = 60
ENROLL_CONTEXT = "regista-enroll/v1"
AUTH_CONTEXT = "regista-agent-auth/v1"


# --- what is signed ---------------------------------------------------------------------------
# The agent builds the same bytes (agent/tests has a fixed vector). The audience is the API's
# public URL, so a signature made for staging cannot be replayed against production.


def audience(api_public_url: str) -> str:
    return api_public_url.rstrip("/")


def enroll_message(key_hash: bytes, aud: str) -> bytes:
    return f"{ENROLL_CONTEXT}\n{key_hash.hex()}\n{aud}".encode()


def auth_message(machine_id: uuid.UUID, nonce_b64: str, aud: str) -> bytes:
    return f"{AUTH_CONTEXT}\n{machine_id}\n{nonce_b64}\n{aud}".encode()


def _verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True


def _b64(value: str, size: int) -> bytes | None:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == size else None


# --- models -----------------------------------------------------------------------------------

AgentVersion = Annotated[str, Field(min_length=1, max_length=32)]
Short = Annotated[str, Field(max_length=100)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OsInfo(BaseModel):
    """A fixed schema: unknown keys from a newer agent are dropped, never stored."""

    model_config = ConfigDict(extra="ignore")
    system: Short | None = None
    release: Short | None = None
    hostname: Short | None = None
    python: Short | None = None


_OS_EXAMPLE: JsonDict = {
    "system": "Windows",
    "release": "11",
    "hostname": "ESTACAO-01",
    "python": "3.13.1",
}


class EnrollRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "key": "rgk_exemplo",
                    "public_key": base64.b64encode(bytes(32)).decode(),
                    "proof": base64.b64encode(bytes(64)).decode(),
                    "agent_version": "0.1.0",
                    "os_info": _OS_EXAMPLE,
                }
            ]
        },
    )
    key: Annotated[str, Field(min_length=1, max_length=128)]
    public_key: Annotated[str, Field(min_length=1, max_length=64)]
    proof: Annotated[str, Field(min_length=1, max_length=128)]
    agent_version: AgentVersion
    os_info: OsInfo


class EnrollResponse(BaseModel):
    machine_id: uuid.UUID
    # The client of this machine. The agent keeps it in its protected folder and compares it with
    # the client named in every signed package it is asked to run (ADR 0021).
    tenant_id: uuid.UUID
    mode: str
    heartbeat_seconds: int


class ChallengeRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"machine_id": "00000000-0000-4000-8000-000000000000"}]},
    )
    machine_id: uuid.UUID


class ChallengeResponse(BaseModel):
    nonce: str
    expires_in: int


class TokenRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "machine_id": "00000000-0000-4000-8000-000000000000",
                    "nonce": base64.b64encode(bytes(32)).decode(),
                    "signature": base64.b64encode(bytes(64)).decode(),
                }
            ]
        },
    )
    machine_id: uuid.UUID
    nonce: Annotated[str, Field(min_length=1, max_length=64)]
    signature: Annotated[str, Field(min_length=1, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105  (the HTTP auth scheme name, not a secret)
    expires_in: int


class HeartbeatRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"agent_version": "0.1.0", "os_info": _OS_EXAMPLE, "interactive_session": True}
            ]
        },
    )
    agent_version: AgentVersion
    os_info: OsInfo
    # Accepted for the session mode, where the machine needs a logged-in Windows user; it is
    # not stored in M2.
    interactive_session: bool = False
    # The run the agent is busy with, if any. The server answers `cancellations` with it when the
    # panel asked to cancel it or when it already ended on the server side (machine lost).
    current_job_id: uuid.UUID | None = None
    # The local kill switch is on: the agent takes no new runs. Only an indication for the panel;
    # nothing in the panel can pause or resume a machine.
    paused: bool = False


class HeartbeatResponse(BaseModel):
    server_time: datetime
    heartbeat_seconds: int
    mode: str
    # Runs of this machine that must stop now (stop the robot, then report cancelled).
    cancellations: list[str]


def _invalid_key() -> HTTPException:
    return api_error(401, "invalid_enrollment_key")


def _invalid_credentials() -> HTTPException:
    return api_error(401, "invalid_credentials")


# --- enroll -----------------------------------------------------------------------------------


@router.post("/enroll", response_model=EnrollResponse, dependencies=[Depends(PublicRoute())])
async def enroll(body: EnrollRequest, request: Request) -> EnrollResponse:
    state = get_state(request)
    s = state.settings
    ip = client_ip(request, s)
    await limit(state, "agent-enroll-ip", ip or "unknown", 60, s.rate_agent_enroll_ip_per_minute)

    key_hash = hash_token(body.key)
    public_key = _b64(body.public_key, 32)
    proof = _b64(body.proof, 64)
    if public_key is None or proof is None or is_weak_public_key(public_key):
        raise _invalid_key()

    # Before the tenant is known: the only path is the minimal SECURITY DEFINER lookup.
    async with tenant_session(state.factory) as db:
        found = (
            (await db.execute(text("SELECT * FROM app.lookup_enrollment_key(:h)"), {"h": key_hash}))
            .mappings()
            .first()
        )
    now = datetime.now(UTC)
    if (
        found is None
        or found["used_at"] is not None
        or found["revoked_at"] is not None
        or found["expires_at"] <= now
        or found["machine_status"] == "revoked"
        or not _verify(public_key, proof, enroll_message(key_hash, audience(s.api_public_url)))
    ):
        raise _invalid_key()

    async with tenant_session(state.factory, tenant_id=found["tenant_id"]) as db:
        # Atomic burn: of two simultaneous calls with the same key, exactly one gets a row.
        burned = (
            await db.execute(
                text(
                    "UPDATE enrollment_keys SET used_at = now() WHERE id = :k AND used_at IS NULL"
                    " AND revoked_at IS NULL AND expires_at > now() RETURNING created_by"
                ),
                {"k": found["key_id"]},
            )
        ).first()
        if burned is None:
            raise _invalid_key()
        machine = (
            await db.execute(
                text("SELECT status, enrolled_at, mode FROM machines WHERE id = :m FOR UPDATE"),
                {"m": found["machine_id"]},
            )
        ).first()
        if machine is None or machine.status == "revoked":
            # Raising rolls the burn back too: a revoked machine consumes nothing.
            raise _invalid_key()
        first_time = machine.enrolled_at is None
        await db.execute(
            text(
                "UPDATE machines SET public_key = :k,"
                " credential_version = credential_version + 1, status = 'online',"
                " last_seen_at = now(), enrolled_at = coalesce(enrolled_at, now()),"
                " agent_version = :v, os_info = CAST(:os AS jsonb), challenge_hash = NULL,"
                " challenge_expires_at = NULL, updated_at = now() WHERE id = :m"
            ),
            {
                "k": public_key,
                "v": body.agent_version,
                "os": body.os_info.model_dump_json(exclude_none=True),
                "m": found["machine_id"],
            },
        )
        kind = "enrolled" if first_time else "re_enrolled"
        await service.record_event(
            db,
            tenant_id=found["tenant_id"],
            machine_id=found["machine_id"],
            kind=kind,
            metadata={"agent_version": body.agent_version},
        )
        await audit.record(
            db,
            tenant_id=found["tenant_id"],
            actor_type="machine",
            actor_id=found["machine_id"],
            action=f"machine.{kind}",
            target_type="machine",
            target_id=found["machine_id"],
            metadata={
                "key_id": str(found["key_id"]),
                "key_created_by": str(burned.created_by) if burned.created_by else None,
            },
            ip=ip,
        )
    return EnrollResponse(
        machine_id=found["machine_id"],
        tenant_id=found["tenant_id"],
        mode=machine.mode,
        heartbeat_seconds=s.heartbeat_seconds,
    )


# --- challenge and token ----------------------------------------------------------------------


async def _credential(
    state: AppState, machine_id: uuid.UUID, ip: str | None
) -> tuple[uuid.UUID, bytes, int]:
    """Rate limit, then the machine's credential. Any machine that cannot log in answers the same
    401: unknown, still pending or revoked."""
    s = state.settings
    await limit(state, "agent-auth-ip", ip or "unknown", 60, s.rate_agent_auth_ip_per_minute)
    await limit(
        state, "agent-auth-machine", str(machine_id), 60, s.rate_agent_auth_machine_per_minute
    )
    async with tenant_session(state.factory) as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM app.lookup_machine_credential(:m)"), {"m": machine_id}
                )
            )
            .mappings()
            .first()
        )
    if row is None or row["status"] not in ("online", "offline") or row["public_key"] is None:
        raise _invalid_credentials()
    return row["tenant_id"], bytes(row["public_key"]), row["credential_version"]


@router.post("/challenge", response_model=ChallengeResponse, dependencies=[Depends(PublicRoute())])
async def challenge(body: ChallengeRequest, request: Request) -> ChallengeResponse:
    state = get_state(request)
    tenant_id, _, _ = await _credential(state, body.machine_id, client_ip(request, state.settings))
    nonce = base64.b64encode(os.urandom(32)).decode()
    async with tenant_session(state.factory, tenant_id=tenant_id) as db:
        # A new challenge replaces the previous one: only the latest nonce can be exchanged.
        stored = (
            await db.execute(
                text(
                    "UPDATE machines SET challenge_hash = :h,"
                    " challenge_expires_at = now() + make_interval(secs => :ttl)"
                    " WHERE id = :m AND status IN ('online', 'offline') RETURNING id"
                ),
                {
                    "h": hashlib.sha256(nonce.encode()).digest(),
                    "ttl": CHALLENGE_SECONDS,
                    "m": body.machine_id,
                },
            )
        ).first()
    if stored is None:
        raise _invalid_credentials()
    return ChallengeResponse(nonce=nonce, expires_in=CHALLENGE_SECONDS)


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(PublicRoute())])
async def token(body: TokenRequest, request: Request) -> TokenResponse:
    state = get_state(request)
    s = state.settings
    tenant_id, public_key, _ = await _credential(state, body.machine_id, client_ip(request, s))
    signature = _b64(body.signature, 64)
    # The signature is checked before the nonce is consumed, so garbage sent by someone who only
    # knows the machine id cannot burn the legitimate agent's challenge.
    if signature is None or not _verify(
        public_key,
        signature,
        auth_message(body.machine_id, body.nonce, audience(s.api_public_url)),
    ):
        raise _invalid_credentials()

    async with tenant_session(state.factory, tenant_id=tenant_id) as db:
        # Single use: the first exchange clears the hash, so the same signature never works twice.
        consumed = (
            await db.execute(
                text(
                    "UPDATE machines SET challenge_hash = NULL WHERE id = :m"
                    " AND challenge_hash = :h AND challenge_expires_at > now()"
                    " AND status IN ('online', 'offline') RETURNING credential_version"
                ),
                {"m": body.machine_id, "h": hashlib.sha256(body.nonce.encode()).digest()},
            )
        ).first()
    if consumed is None:
        raise _invalid_credentials()

    lifetime = timedelta(minutes=s.agent_token_minutes)
    access_token, _ = issue_machine_token(
        state.keys,
        machine_id=body.machine_id,
        tenant_id=tenant_id,
        credential_version=consumed.credential_version,
        lifetime=lifetime,
    )
    return TokenResponse(access_token=access_token, expires_in=int(lifetime.total_seconds()))


# --- heartbeat --------------------------------------------------------------------------------

# True while no `first_signal` came after the latest enrollment (or re-enrollment).
_FIRST_SIGNAL_PENDING = text(
    "SELECT NOT EXISTS ("
    " SELECT 1 FROM machine_events f WHERE f.machine_id = :m AND f.kind = 'first_signal'"
    " AND f.created_at >= coalesce((SELECT max(e.created_at) FROM machine_events e"
    "   WHERE e.machine_id = :m AND e.kind IN ('enrolled', 're_enrolled')), '-infinity'))"
)


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    body: HeartbeatRequest, machine: Annotated[MachineAuth, Depends(MachineRoute())]
) -> HeartbeatResponse:
    async with machine.session() as db:
        await _record_signal(db, machine, body)
        cancellations = await job_service.cancellations(db, machine.machine_id, body.current_job_id)
        for job_id in await job_service.release_orphans(
            db,
            tenant_id=machine.tenant_id,
            machine_id=machine.machine_id,
            current_job_id=body.current_job_id,
            older_than_seconds=machine.state.settings.assigned_orphan_seconds,
        ):
            await audit.record(
                db,
                tenant_id=machine.tenant_id,
                actor_type="system",
                actor_id=None,
                action="job.orphan_released",
                target_type="job",
                target_id=job_id,
                metadata={"machine_id": str(machine.machine_id)},
                ip=machine.ip,
            )
    return HeartbeatResponse(
        server_time=datetime.now(UTC),
        heartbeat_seconds=machine.state.settings.heartbeat_seconds,
        mode=machine.mode,
        cancellations=[str(j) for j in cancellations],
    )


async def _record_signal(db: AsyncSession, machine: MachineAuth, body: HeartbeatRequest) -> None:
    before = (
        await db.execute(
            text("SELECT status, agent_version FROM machines WHERE id = :m FOR UPDATE"),
            {"m": machine.machine_id},
        )
    ).first()
    if before is None or before.status == "pending":
        raise api_error(401, "not_authenticated")
    if before.status == "revoked":
        raise api_error(401, "machine_revoked")

    first: bool = (await db.execute(_FIRST_SIGNAL_PENDING, {"m": machine.machine_id})).scalar_one()
    await db.execute(
        text(
            "UPDATE machines SET last_seen_at = now(), status = 'online', agent_version = :v,"
            " os_info = CAST(:os AS jsonb), paused_locally = :p, updated_at = now()"
            " WHERE id = :m"
        ),
        {
            "p": body.paused,
            "v": body.agent_version,
            "os": body.os_info.model_dump_json(exclude_none=True),
            "m": machine.machine_id,
        },
    )
    events: list[tuple[str, dict[str, Any]]] = []
    if first:
        events.append(("first_signal", {}))
    elif before.status == "offline":
        events.append(("came_back", {}))
    if before.agent_version is not None and before.agent_version != body.agent_version:
        events.append(("agent_updated", {"from": before.agent_version, "to": body.agent_version}))
    for kind, metadata in events:
        await service.record_event(
            db,
            tenant_id=machine.tenant_id,
            machine_id=machine.machine_id,
            kind=kind,
            metadata=metadata,
        )
