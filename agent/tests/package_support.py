"""Signed packages for the agent tests. The production key never exists here: tests sign with a key
made on the spot and make the agent trust it through the development-only override."""

import base64
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from regista_agent.jobapi import PackageOffer
from regista_pkg import Manifest, key_id_of, parse_key_file, sign

ROBOT = b"print('hello from a signed robot')\n"


@dataclass(frozen=True)
class TestKey:
    __test__ = False  # not a test class, whatever pytest thinks of its name

    private: Ed25519PrivateKey
    key_id: str
    keys_file: Path

    def trusted(self) -> dict[str, Ed25519PublicKey]:
        return parse_key_file(self.keys_file.read_text("utf-8"))


def new_key(folder: Path) -> TestKey:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key_id = key_id_of(raw)
    folder.mkdir(parents=True, exist_ok=True)
    keys_file = folder / "trusted.json"
    keys_file.write_text(json.dumps({key_id: base64.b64encode(raw).decode()}), encoding="utf-8")
    return TestKey(private, key_id, keys_file)


@dataclass(frozen=True)
class Built:
    data: bytes
    signature: str
    manifest: Manifest
    sha256: str
    url: str

    def offer(self, version_id: str = "v-1", **over: object) -> PackageOffer:
        fields: dict[str, object] = {
            "version_id": version_id,
            "version": self.manifest.version,
            "package_name": self.manifest.package_name,
            "sha256": self.sha256,
            "size_bytes": len(self.data),
            "key_id": "0" * 16,
            "signature_doc": self.signature,
            "download_url": self.url,
        }
        fields.update(over)
        return PackageOffer(**fields)  # type: ignore[arg-type]


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


def build(
    key: TestKey,
    *,
    tenant_id: uuid.UUID,
    package_name: str = "fake_bot",
    version: str = "1.0.0",
    members: dict[str, bytes] | None = None,
    inner: Manifest | None = None,
    raw: bytes | None = None,
    signer: Ed25519PrivateKey | None = None,
) -> Built:
    """`raw` signs different bytes than a zip made from the manifest; `signer` forges."""
    manifest = Manifest(
        tenant_id=str(tenant_id), package_name=package_name, version=version, python="3.13.5"
    )
    data = raw if raw is not None else build_zip(inner or manifest, members)
    sha256 = hashlib.sha256(data).hexdigest()
    document = sign(
        signer or key.private, manifest, sha256=sha256, size=len(data), key_id=key.key_id
    ).decode("utf-8")
    return Built(data, document, manifest, sha256, f"https://storage.test/{uuid.uuid4().hex}")
