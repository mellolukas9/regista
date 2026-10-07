"""Key ids and the list of trusted public keys."""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from regista_pkg.reasons import PackageError
from regista_pkg.trusted_keys import TRUSTED_PUBLIC_KEYS


def key_id_of(public_raw: bytes) -> str:
    """The first 16 hex characters of the sha256 of the raw public key."""
    return hashlib.sha256(public_raw).hexdigest()[:16]


def _public_key(raw: bytes) -> Ed25519PublicKey:
    if len(raw) != 32:
        raise ValueError("an Ed25519 public key has 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def parse_key_file(text: str) -> dict[str, Ed25519PublicKey]:
    """`{"<key_id>": "<base64 public key>"}`. The id must be the one the key really has: a file
    cannot name a key after another one."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        keys: dict[str, Ed25519PublicKey] = {}
        for key_id, encoded in data.items():
            raw = base64.b64decode(encoded, validate=True)
            if key_id_of(raw) != key_id:
                raise ValueError(f"{key_id} is not the id of its key")
            keys[key_id] = _public_key(raw)
        return keys
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError(f"invalid trusted keys file: {exc}") from exc


def load_trusted_keys(
    extra: Mapping[str, Ed25519PublicKey] | None = None,
) -> dict[str, Ed25519PublicKey]:
    """The compiled-in keys plus `extra` (a development override, which the caller only passes
    outside production). The compiled-in keys are checked the same way as a file."""
    keys = parse_key_file(json.dumps(TRUSTED_PUBLIC_KEYS))
    for key_id, key in (extra or {}).items():
        keys.setdefault(key_id, key)
    return keys


def require_trusted(key_id: str, keys: Mapping[str, Ed25519PublicKey]) -> Ed25519PublicKey:
    key = keys.get(key_id)
    if key is None:
        raise PackageError("unknown_key", key_id)
    return key
