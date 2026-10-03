"""Password hashing, opaque tokens and the password policy (docs/specs/security.md)."""

import asyncio
import hashlib
import re
import secrets
from functools import lru_cache
from importlib import resources

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()
# Verified when the account does not exist, so "unknown e-mail" and "wrong password"
# take the same time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-effort check: a missing hash still costs one argon2 verification."""
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return password_hash is not None


async def hash_password_async(password: str) -> str:
    """argon2 is deliberately slow; keep it off the event loop."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    """Opaque URL-safe token with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """sha256 of a token; only this digest is stored (sessions, invitations)."""
    return hashlib.sha256(token.encode()).digest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


@lru_cache
def _common_passwords() -> frozenset[str]:
    text = resources.files("regista_api.core").joinpath("common_passwords.txt").read_text("utf-8")
    return frozenset(text.split())


_TRAILING_NON_LETTERS = re.compile(r"[^a-z]+$")
_LEADING_NON_LETTERS = re.compile(r"^[^a-z]+")


def password_problem(password: str, *, current_hash: str | None = None) -> str | None:
    """Return a stable error code for an unacceptable password, or None when it is fine.

    Codes: `too_short`, `too_common`, `same_as_current`.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return "too_short"
    lowered = password.lower()
    common = _common_passwords()
    candidates = {
        lowered,
        _TRAILING_NON_LETTERS.sub("", lowered),
        _LEADING_NON_LETTERS.sub("", lowered),
    }
    if any(c and c in common for c in candidates):
        return "too_common"
    if current_hash is not None and verify_password(current_hash, password):
        return "same_as_current"
    return None


_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_recovery_code() -> str:
    """`XXXX-XXXX` from an alphabet without look-alike characters."""
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_recovery_code(code: str) -> str:
    return code.replace("-", "").replace(" ", "").upper()
