"""Getting a robot package onto this machine and trusting it only as far as it has earned
(docs/specs/agent.md, "Execução de um robô"; ADR 0021).

The order is the point. Nothing the server says is believed:

1. the signature document is checked against the keys compiled into the agent (and only those);
2. the signed manifest must name *this* machine's client (from the identity written at enrollment,
   never from the answer about the run), the package of the bot being run and the version of the
   run. A package of another client is refused even when the server sends it, and before it is
   downloaded;
3. the file is downloaded to a temporary name, and its size and sha256 must be exactly what was
   signed; only then does it get its place in the cache. A file already in the cache is checked
   again every time: the cache is not trusted either;
4. only after all that is the zip opened, entry by entry, with `regista_pkg.archive` (zip slip,
   links, Windows names, bombs), and its inner manifest must be the signed one.

Every refusal is a `regista_pkg.PackageError` whose `reason` is from the closed list the panel
has a fixed text for.
"""

import contextlib
import logging
import os
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from regista_agent.jobapi import JobApi, PackageOffer
from regista_pkg import (
    PackageError,
    SignedManifest,
    parse_signature_doc,
    verify,
    verify_file_hash,
)
from regista_pkg import archive as arch

log = logging.getLogger("regista_agent")

MAX_PACKAGE_BYTES = 200 * 1024 * 1024
KEEP_VERSIONS = 3


@dataclass(frozen=True)
class Expected:
    """What this run is supposed to be running, from sources the server cannot rewrite."""

    tenant_id: uuid.UUID  # the identity written at enrollment
    package_name: str  # the bot of the run
    version: str  # the version the run was taken with


@dataclass(frozen=True)
class VerifiedPackage:
    path: Path  # the file in the cache, whose hash and signature were just checked
    signed: SignedManifest


class PackageCache:
    """`<root>/<package_name>/<sha256>.rgpkg`, and the checks that make a file in it usable."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, package_name: str, sha256: str) -> Path:
        return self.root / package_name / f"{sha256}.rgpkg"

    def fetch(
        self,
        api: JobApi,
        offer: PackageOffer,
        expected: Expected,
        keys: Mapping[str, Ed25519PublicKey],
    ) -> VerifiedPackage:
        """Verify what the server offers, then bring the file in. Raises `PackageError`."""
        signed, signature = parse_signature_doc(offer.signature_doc.encode("utf-8"))
        verify(signed, signature, keys)
        manifest = signed.manifest
        if manifest.tenant_id != str(expected.tenant_id):
            raise PackageError("wrong_client", "the package names another client")
        if manifest.package_name != expected.package_name:
            raise PackageError("wrong_package")
        if manifest.version != expected.version:
            raise PackageError("wrong_version")
        # The offer must say what the signature says; it is only a convenience.
        if offer.sha256 != signed.sha256 or offer.size_bytes != signed.size:
            raise PackageError("hash_mismatch", "the offer differs from the signed manifest")
        if signed.size > MAX_PACKAGE_BYTES:
            raise PackageError("too_large")

        target = self.path_for(manifest.package_name, signed.sha256)
        if target.is_file():
            try:
                verify_file_hash(target, signed)
                return VerifiedPackage(target, signed)
            except PackageError:
                log.warning("cached package %s no longer matches its hash; fetching again", target)
                target.unlink(missing_ok=True)

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            api.download_package(offer.download_url, partial, signed.size)
            verify_file_hash(partial, signed)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        return VerifiedPackage(target, signed)

    def prune(self, package_name: str, *, protect: set[str], keep: int = KEEP_VERSIONS) -> None:
        """Keep the `keep` most recently used packages of this robot (and never one in `protect`,
        a hash that is running now); the rest, with what was unpacked from them, is removed."""
        folder = self.root / package_name
        if not folder.is_dir():
            return
        files = sorted(folder.glob("*.rgpkg"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[keep:]:
            if stale.stem in protect:
                continue
            with contextlib.suppress(OSError):
                stale.unlink()


def extract(package: VerifiedPackage, destination: Path) -> None:
    """Unpack into an empty `destination`. The file is checked once more right before it is
    opened, and the manifest inside must be the one that was signed."""
    verify_file_hash(package.path, package.signed)
    with arch.open_package(package.path) as zf:
        try:
            arch.safe_extract(zf, destination)
            if arch.read_inner_manifest(zf) != package.signed.manifest:
                raise PackageError("malformed_package", "manifest.json differs from the signed one")
        except PackageError:
            shutil.rmtree(destination, ignore_errors=True)  # nothing half-trusted stays behind
            raise
