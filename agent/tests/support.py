"""Shared by the agent tests that need a server to enroll against."""

import base64
import json
import uuid

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from regista_agent import protocol
from regista_agent.transport import HttpSession

SERVER = "https://regista.exemplo.com.br"
KEY = "rgk_chave-de-teste-123"
MACHINE_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()


def _public(private_key: object) -> bytes:
    assert hasattr(private_key, "public_key")
    return private_key.public_key().public_bytes(  # type: ignore[no-any-return]
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


class EnrollServer:
    """The enrollment endpoint: it checks the proof of possession the way the real one does."""

    def __init__(self, *, mode: str = "service", refuse: bool = False, status: int = 200) -> None:
        self.mode = mode
        self.refuse = refuse
        self.status = status
        self.calls = 0
        self.bodies: list[dict[str, object]] = []
        self.proof_valid: list[bool] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert request.url.path == "/agent/enroll"
        body = json.loads(request.content)
        self.bodies.append(body)
        public = base64.b64decode(body["public_key"])
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(
                base64.b64decode(body["proof"]), protocol.enroll_message(body["key"], SERVER)
            )
            self.proof_valid.append(True)
        except InvalidSignature:
            self.proof_valid.append(False)
        if self.status != 200:
            return httpx.Response(self.status)
        if self.refuse or not self.proof_valid[-1]:
            return httpx.Response(401, json={"detail": {"code": "invalid_enrollment_key"}})
        return httpx.Response(
            200,
            json={
                "machine_id": str(MACHINE_ID),
                "tenant_id": str(TENANT_ID),
                "mode": self.mode,
                "heartbeat_seconds": 30,
            },
        )

    def session(self) -> HttpSession:
        client = httpx.Client(base_url=SERVER, transport=httpx.MockTransport(self.handler))
        return HttpSession(client, sleep=lambda seconds: None)
