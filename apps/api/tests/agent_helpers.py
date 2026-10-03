"""A stand-in for the real agent in tests: a key pair and the calls of the agent protocol."""

import base64
import secrets
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_api.core.security import hash_token
from regista_api.machines.agent import auth_message, enroll_message

OS = {"system": "Windows", "release": "11", "hostname": "PC-01", "python": "3.13.1"}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fresh_ip() -> dict[str, str]:
    """Rate limits are per address and the test database is shared: every call gets its own."""
    return {"X-Forwarded-For": f"203.0.{secrets.randbelow(256)}.{1 + secrets.randbelow(254)}"}


class AgentSim:
    def __init__(
        self, client: httpx.AsyncClient, aud: str, private: Ed25519PrivateKey | None = None
    ) -> None:
        self.client = client
        self.aud = aud
        self.private = private or Ed25519PrivateKey.generate()
        self.machine_id = ""
        self.token = ""

    def enroll_body(
        self, key: str, *, aud: str | None = None, agent_version: str = "0.1.0"
    ) -> dict[str, Any]:
        proof = self.private.sign(enroll_message(hash_token(key), aud or self.aud))
        return {
            "key": key,
            "public_key": b64(raw_public(self.private)),
            "proof": b64(proof),
            "agent_version": agent_version,
            "os_info": OS,
        }

    async def enroll(
        self, key: str, *, ip: dict[str, str] | None = None, agent_version: str = "0.1.0"
    ) -> httpx.Response:
        r = await self.client.post(
            "/agent/enroll",
            json=self.enroll_body(key, agent_version=agent_version),
            headers=ip or fresh_ip(),
        )
        if r.status_code == 200:
            self.machine_id = r.json()["machine_id"]
        return r

    async def challenge(self, machine_id: str | None = None) -> httpx.Response:
        return await self.client.post(
            "/agent/challenge",
            json={"machine_id": machine_id or self.machine_id},
            headers=fresh_ip(),
        )

    def sign(self, nonce: str, *, machine_id: str | None = None, aud: str | None = None) -> str:
        message = auth_message(uuid.UUID(machine_id or self.machine_id), nonce, aud or self.aud)
        return b64(self.private.sign(message))

    async def exchange(
        self, nonce: str, signature: str, machine_id: str | None = None
    ) -> httpx.Response:
        return await self.client.post(
            "/agent/token",
            json={
                "machine_id": machine_id or self.machine_id,
                "nonce": nonce,
                "signature": signature,
            },
            headers=fresh_ip(),
        )

    async def login(self) -> str:
        challenge = await self.challenge()
        assert challenge.status_code == 200, challenge.text
        nonce = challenge.json()["nonce"]
        r = await self.exchange(nonce, self.sign(nonce))
        assert r.status_code == 200, r.text
        self.token = r.json()["access_token"]
        return self.token

    async def heartbeat(self, token: str | None = None, **overrides: Any) -> httpx.Response:
        body = {"agent_version": "0.1.0", "os_info": OS, "interactive_session": True, **overrides}
        return await self.client.post(
            "/agent/heartbeat",
            json=body,
            headers={"Authorization": f"Bearer {token or self.token}"},
        )
