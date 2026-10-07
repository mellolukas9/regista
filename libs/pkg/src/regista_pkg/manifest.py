"""The manifest: what a signature vouches for.

`Manifest` is what the package itself carries (`manifest.json` inside the zip). `SignedManifest`
adds what can only be known after the zip exists: its sha256 and size, and the key that signed.
The message that is signed is the canonical JSON of the signed manifest under a fixed prefix.
"""

import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from regista_pkg.reasons import PackageError

FORMAT = "regista-package-v1"

_PACKAGE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_REVISION = re.compile(r"^[0-9]{1,8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[0-9a-f]{16}$")

_MANIFEST_KEYS = {
    "tenant_id",
    "package_name",
    "version",
    "python",
    "playwright",
    "chromium_revision",
}
_SIGNED_KEYS = _MANIFEST_KEYS | {"sha256", "size", "key_id"}


def _bad(detail: str) -> PackageError:
    return PackageError("malformed_package", detail)


def _check(value: object, pattern: re.Pattern[str], name: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _bad(f"invalid {name}")


@dataclass(frozen=True)
class Manifest:
    tenant_id: str
    package_name: str
    version: str
    python: str  # exact, e.g. 3.13.5
    playwright: str | None = None  # exact, e.g. 1.55.0; None for a robot without a browser
    chromium_revision: str | None = None

    def __post_init__(self) -> None:
        try:
            if str(uuid.UUID(self.tenant_id)) != self.tenant_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise _bad("invalid tenant_id") from None
        _check(self.package_name, _PACKAGE_NAME, "package_name")
        _check(self.version, _VERSION, "version")
        _check(self.python, _VERSION, "python")
        _check(self.playwright, _VERSION, "playwright", optional=True)
        _check(self.chromium_revision, _REVISION, "chromium_revision", optional=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True).encode("utf-8")

    @classmethod
    def from_dict(cls, data: object) -> "Manifest":
        if not isinstance(data, dict) or set(data) != _MANIFEST_KEYS:
            raise _bad("manifest keys")
        return cls(**data)

    @classmethod
    def from_json(cls, raw: bytes) -> "Manifest":
        try:
            return cls.from_dict(json.loads(raw))
        except (ValueError, TypeError) as exc:
            if isinstance(exc, PackageError):
                raise
            raise _bad("manifest is not JSON") from None


@dataclass(frozen=True)
class SignedManifest:
    manifest: Manifest
    sha256: str
    size: int
    key_id: str

    def __post_init__(self) -> None:
        _check(self.sha256, _SHA256, "sha256")
        _check(self.key_id, _KEY_ID, "key_id")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 1:
            raise _bad("invalid size")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.manifest.to_dict(),
            "sha256": self.sha256,
            "size": self.size,
            "key_id": self.key_id,
        }

    @classmethod
    def from_dict(cls, data: object) -> "SignedManifest":
        if not isinstance(data, dict) or set(data) != _SIGNED_KEYS:
            raise _bad("signed manifest keys")
        inner = {k: data[k] for k in _MANIFEST_KEYS}
        return cls(
            manifest=Manifest.from_dict(inner),
            sha256=data["sha256"],
            size=data["size"],
            key_id=data["key_id"],
        )


def canonical_message(signed: SignedManifest) -> bytes:
    """The exact bytes that are signed: a fixed prefix and the canonical JSON (sorted keys, no
    spaces, UTF-8), so two implementations cannot disagree about it."""
    body = json.dumps(
        signed.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return FORMAT.encode("ascii") + b"\n" + body
