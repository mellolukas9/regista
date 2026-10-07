"""Signed packages for the tests: a throwaway key and a builder that mirrors `regista-pack`.

The production key never exists here. Tests sign with a key generated on the spot and make the API
(and the agent) trust it through the development-only override.
"""

import base64
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_pkg import Manifest, key_id_of, sign

ROBOT = b"print('hello from a signed robot')\n"


@dataclass(frozen=True)
class SigningKey:
    private: Ed25519PrivateKey
    key_id: str
    keys_file: Path  # the public key, in the format of REGISTA_DEV_TRUSTED_KEYS


def new_signing_key(folder: Path) -> SigningKey:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key_id = key_id_of(raw)
    folder.mkdir(parents=True, exist_ok=True)
    keys_file = folder / "trusted-test-keys.json"
    keys_file.write_text(json.dumps({key_id: base64.b64encode(raw).decode()}), encoding="utf-8")
    return SigningKey(private, key_id, keys_file)


@dataclass(frozen=True)
class SignedPackage:
    package: bytes  # the .rgpkg
    signature: str  # the .rgsig document
    manifest: Manifest
    sha256: str


def build_zip(manifest: Manifest, members: dict[str, bytes] | None = None) -> bytes:
    entries: dict[str, bytes] = {
        "manifest.json": manifest.to_json(),
        "requirements.lock": b"",
        "bot/main.py": ROBOT,
    }
    entries.update(members or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, content)
    return buffer.getvalue()


def make_package(
    key: SigningKey,
    *,
    tenant_id: uuid.UUID | str,
    package_name: str,
    version: str = "1.0.0",
    members: dict[str, bytes] | None = None,
    inner: Manifest | None = None,
    python: str = "3.13.5",
    claimed_size: int | None = None,
    signer: Ed25519PrivateKey | None = None,
) -> SignedPackage:
    """A package signed by `key` (or by `signer`, claiming `key`'s id, to forge one).

    `inner` puts a different manifest inside the zip than the one that is signed;
    `claimed_size` signs a size the file does not have (for the size limit)."""
    manifest = Manifest(
        tenant_id=str(tenant_id), package_name=package_name, version=version, python=python
    )
    data = build_zip(inner or manifest, members)
    sha256 = hashlib.sha256(data).hexdigest()
    document = sign(
        signer or key.private,
        manifest,
        sha256=sha256,
        size=claimed_size or len(data),
        key_id=key.key_id,
    )
    return SignedPackage(data, document.decode("utf-8"), manifest, sha256)
