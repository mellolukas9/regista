"""The package signature: what is signed, who is trusted, and every way to get it wrong."""

import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from regista_pkg import (
    FORMAT,
    REASONS,
    Manifest,
    PackageError,
    SignedManifest,
    canonical_message,
    key_id_of,
    load_trusted_keys,
    parse_key_file,
    parse_signature_doc,
    sign,
    verify,
    verify_file_hash,
)

TENANT = str(uuid.uuid4())


def _raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _manifest(**over: Any) -> Manifest:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "package_name": "demo_busca",
        "version": "1.2.0",
        "python": "3.13.5",
        "playwright": "1.55.0",
        "chromium_revision": "1187",
    }
    base.update(over)
    return Manifest(**base)


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _signed_doc(key: Ed25519PrivateKey, manifest: Manifest | None = None) -> bytes:
    return sign(
        key, manifest or _manifest(), sha256="a" * 64, size=1234, key_id=key_id_of(_raw(key))
    )


def _trusted(key: Ed25519PrivateKey) -> dict[str, Ed25519PublicKey]:
    return {key_id_of(_raw(key)): key.public_key()}


def test_a_good_signature_verifies(key: Ed25519PrivateKey) -> None:
    signed, signature = parse_signature_doc(_signed_doc(key))
    verify(signed, signature, _trusted(key))
    assert signed.manifest == _manifest() and signed.sha256 == "a" * 64 and signed.size == 1234


def test_the_message_is_a_fixed_prefix_and_canonical_json(key: Ed25519PrivateKey) -> None:
    signed, _ = parse_signature_doc(_signed_doc(key))
    prefix, body = canonical_message(signed).split(b"\n", 1)
    assert prefix == FORMAT.encode()
    assert body == json.dumps(signed.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    assert b" " not in body


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", str(uuid.uuid4())),
        ("package_name", "other_robot"),
        ("version", "1.2.1"),
        ("python", "3.12.9"),
        ("playwright", "1.56.0"),
        ("chromium_revision", "1200"),
        ("sha256", "b" * 64),
        ("size", 99),
    ],
)
def test_changing_any_signed_field_breaks_the_signature(
    key: Ed25519PrivateKey, field: str, value: Any
) -> None:
    doc = json.loads(_signed_doc(key))
    doc["manifest"][field] = value
    signed, signature = parse_signature_doc(json.dumps(doc).encode())
    with pytest.raises(PackageError) as caught:
        verify(signed, signature, _trusted(key))
    assert caught.value.reason == "signature_invalid"


def test_a_key_that_is_not_trusted_is_refused_as_unknown(key: Ed25519PrivateKey) -> None:
    signed, signature = parse_signature_doc(_signed_doc(key))
    with pytest.raises(PackageError) as caught:
        verify(signed, signature, _trusted(Ed25519PrivateKey.generate()))
    assert caught.value.reason == "unknown_key"


def test_claiming_the_id_of_a_trusted_key_does_not_help(key: Ed25519PrivateKey) -> None:
    """An attacker signs with their own key but writes the id of a trusted one."""
    attacker = Ed25519PrivateKey.generate()
    doc = sign(attacker, _manifest(), sha256="a" * 64, size=1234, key_id=key_id_of(_raw(key)))
    signed, signature = parse_signature_doc(doc)
    with pytest.raises(PackageError, match="signature_invalid"):
        verify(signed, signature, _trusted(key))


def test_a_wrong_signature_is_refused(key: Ed25519PrivateKey) -> None:
    doc = json.loads(_signed_doc(key))
    doc["signature"] = base64.b64encode(b"\x00" * 64).decode()
    signed, signature = parse_signature_doc(json.dumps(doc).encode())
    with pytest.raises(PackageError, match="signature_invalid"):
        verify(signed, signature, _trusted(key))


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"format": "regista-signature-v1"}'])
def test_garbage_is_malformed(raw: bytes) -> None:
    with pytest.raises(PackageError) as caught:
        parse_signature_doc(raw)
    assert caught.value.reason == "malformed_package"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("format",), "regista-signature-v9"),
        (("signature",), "AAAA"),
        (("signature",), "!!!"),
        (("manifest", "tenant_id"), "not-a-uuid"),
        (("manifest", "tenant_id"), TENANT.upper()),
        (("manifest", "package_name"), "../evil"),
        (("manifest", "version"), "1.2"),
        (("manifest", "python"), "3.13"),
        (("manifest", "sha256"), "ZZ"),
        (("manifest", "size"), 0),
        (("manifest", "size"), True),
        (("manifest", "key_id"), "short"),
        (("manifest", "chromium_revision"), "abc"),
    ],
)
def test_malformed_fields_are_refused(
    key: Ed25519PrivateKey, path: tuple[str, ...], value: Any
) -> None:
    doc = json.loads(_signed_doc(key))
    target = doc
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(PackageError) as caught:
        parse_signature_doc(json.dumps(doc).encode())
    assert caught.value.reason == "malformed_package"


def test_an_unexpected_extra_field_is_refused(key: Ed25519PrivateKey) -> None:
    doc = json.loads(_signed_doc(key))
    doc["manifest"]["surprise"] = 1
    with pytest.raises(PackageError, match="malformed_package"):
        parse_signature_doc(json.dumps(doc).encode())


def test_a_robot_without_a_browser_has_no_playwright(key: Ed25519PrivateKey) -> None:
    doc = _signed_doc(key, _manifest(playwright=None, chromium_revision=None))
    signed, signature = parse_signature_doc(doc)
    verify(signed, signature, _trusted(key))
    assert signed.manifest.playwright is None


def test_the_inner_manifest_round_trips() -> None:
    manifest = _manifest()
    assert Manifest.from_json(manifest.to_json()) == manifest
    for bad in (b"{", b"[]", b'{"tenant_id": 1}'):
        with pytest.raises(PackageError, match="malformed_package"):
            Manifest.from_json(bad)


def test_the_file_must_be_exactly_what_was_signed(tmp_path: Path) -> None:
    content = b"zip bytes" * 100
    file = tmp_path / "p.rgpkg"
    file.write_bytes(content)
    signed = SignedManifest(
        _manifest(), sha256=hashlib.sha256(content).hexdigest(), size=len(content), key_id="0" * 16
    )
    verify_file_hash(file, signed)
    file.write_bytes(content[:-1] + b"X")  # same size, other content
    with pytest.raises(PackageError, match="hash_mismatch"):
        verify_file_hash(file, signed)
    file.write_bytes(content + b"!")  # other size
    with pytest.raises(PackageError, match="hash_mismatch"):
        verify_file_hash(file, signed)


def test_key_files_are_checked(key: Ed25519PrivateKey) -> None:
    kid, raw = key_id_of(_raw(key)), base64.b64encode(_raw(key)).decode()
    assert set(parse_key_file(json.dumps({kid: raw}))) == {kid}
    for bad in (
        "nope",
        "[]",
        json.dumps({"0" * 16: raw}),  # an id that is not the key's own
        json.dumps({kid: "!!!"}),
        json.dumps({kid: base64.b64encode(b"short").decode()}),
    ):
        with pytest.raises(ValueError, match="invalid trusted keys file"):
            parse_key_file(bad)


def test_the_compiled_in_keys_are_well_formed() -> None:
    """The list ships empty until the production keys exist (nothing is trusted, so no package
    runs); whatever is listed must parse and carry its own id."""
    assert all(len(k) == 16 for k in load_trusted_keys())


def test_reasons_are_the_closed_list() -> None:
    assert len(set(REASONS)) == len(REASONS) == 9
    with pytest.raises(ValueError, match="unknown reason"):
        PackageError("because I said so")


def test_an_oversized_signature_document_is_refused_before_parsing() -> None:
    with pytest.raises(PackageError, match="too large"):
        parse_signature_doc(b"x" * 70_000)
