"""Signing and verifying (ed25519) and the `.rgsig` document that carries the signature."""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from regista_pkg.keys import require_trusted
from regista_pkg.manifest import Manifest, SignedManifest, canonical_message
from regista_pkg.reasons import PackageError

SIGNATURE_FORMAT = "regista-signature-v1"
MAX_SIGNATURE_DOC_BYTES = 65_536
_CHUNK = 1024 * 1024


def sign(
    private_key: Ed25519PrivateKey, manifest: Manifest, *, sha256: str, size: int, key_id: str
) -> bytes:
    """The `.rgsig` document (UTF-8 JSON) for a package with this hash and size."""
    signed = SignedManifest(manifest=manifest, sha256=sha256, size=size, key_id=key_id)
    signature = private_key.sign(canonical_message(signed))
    doc = {
        "format": SIGNATURE_FORMAT,
        "manifest": signed.to_dict(),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")


def parse_signature_doc(raw: bytes) -> tuple[SignedManifest, bytes]:
    """Read a `.rgsig`. Says nothing about whether the signature is good: see `verify`."""
    if len(raw) > MAX_SIGNATURE_DOC_BYTES:
        raise PackageError("malformed_package", "signature document too large")
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict) or set(doc) != {"format", "manifest", "signature"}:
            raise ValueError("keys")
        if doc["format"] != SIGNATURE_FORMAT:
            raise ValueError("format")
        signature = base64.b64decode(doc["signature"], validate=True)
        if len(signature) != 64:
            raise ValueError("signature length")
    except (ValueError, TypeError, binascii.Error) as exc:
        raise PackageError("malformed_package", f"signature document: {exc}") from None
    return SignedManifest.from_dict(doc["manifest"]), signature


def verify(signed: SignedManifest, signature: bytes, keys: Mapping[str, Ed25519PublicKey]) -> None:
    """Raise `unknown_key` if the signing key is not trusted and `signature_invalid` if the
    signature does not match the signed manifest."""
    key = require_trusted(signed.key_id, keys)
    try:
        key.verify(signature, canonical_message(signed))
    except InvalidSignature:
        raise PackageError("signature_invalid") from None


def sha256_of(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_file_hash(path: Path, signed: SignedManifest) -> None:
    """The file is exactly what was signed (size first, so a huge file is not hashed at all)."""
    if path.stat().st_size != signed.size:
        raise PackageError("hash_mismatch", "size differs")
    digest, size = sha256_of(path)
    if size != signed.size or digest != signed.sha256:
        raise PackageError("hash_mismatch")
