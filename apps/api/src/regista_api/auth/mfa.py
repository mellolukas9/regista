"""TOTP helpers (RFC 6238) with replay protection."""

import secrets
import time
import uuid

import pyotp

from regista_api.core.keys import KeyProvider

STEP_SECONDS = 30
ISSUER = "Regista"


def _clock() -> float:
    """Wall clock; tests replace it to move between TOTP steps without sleeping."""
    return time.time()


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def match_step(secret: str, code: str, *, now: float | None = None) -> int | None:
    """Return the time step the code belongs to (current ±1), or None.

    The caller must reject a step that is not newer than the last accepted one, so a code
    cannot be used twice.
    """
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int((_clock() if now is None else now) // STEP_SECONDS)
    for step in (current, current - 1, current + 1):
        if secrets.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def _aad(user_id: uuid.UUID) -> bytes:
    return str(user_id).encode()


def encrypt_secret(
    keys: KeyProvider, tenant_id: uuid.UUID, user_id: uuid.UUID, secret: str
) -> tuple[bytes, str]:
    return keys.encrypt(tenant_id, secret.encode(), aad=_aad(user_id))


def decrypt_secret(
    keys: KeyProvider, tenant_id: uuid.UUID, user_id: uuid.UUID, ciphertext: bytes, key_id: str
) -> str:
    return keys.decrypt(tenant_id, ciphertext, key_id, aad=_aad(user_id)).decode()
