"""What the agent signs (docs/adr/0018, docs/specs/agent.md).

These are the exact bytes the server verifies (`regista_api/machines/agent.py`). The two sides
are separate packages, so `docs/specs/agent-signing-vector.json` is the contract: both test
suites read it, and a change on one side that is not made on the other breaks a test.

The audience is the API's public URL, so a signature made for one environment is worthless in
another. A trailing slash does not count.
"""

import base64
import hashlib

ENROLL_CONTEXT = "regista-enroll/v1"
AUTH_CONTEXT = "regista-agent-auth/v1"


def audience(server_url: str) -> str:
    return server_url.rstrip("/")


def key_hash(enrollment_key: str) -> bytes:
    """sha256 of the enrollment key: the only form in which the server stores it."""
    return hashlib.sha256(enrollment_key.encode()).digest()


def enroll_message(enrollment_key: str, aud: str) -> bytes:
    return f"{ENROLL_CONTEXT}\n{key_hash(enrollment_key).hex()}\n{aud}".encode()


def auth_message(machine_id: str, nonce_b64: str, aud: str) -> bytes:
    """`nonce_b64` is the nonce exactly as the server sent it (the text, not the bytes)."""
    return f"{AUTH_CONTEXT}\n{machine_id}\n{nonce_b64}\n{aud}".encode()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()
