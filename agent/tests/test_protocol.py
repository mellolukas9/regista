"""The agent signs exactly what the server verifies: the shared vector is the contract."""

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent import protocol

VECTOR = json.loads(
    (Path(__file__).parents[2] / "docs" / "specs" / "agent-signing-vector.json").read_text("utf-8")
)


def _private() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(VECTOR["private_seed_hex"]))


def test_the_key_pair_of_the_vector() -> None:
    public = (
        _private()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    assert protocol.b64(public) == VECTOR["public_key_b64"]


def test_enrollment_proof_matches_the_vector() -> None:
    e = VECTOR["enroll"]
    aud = protocol.audience(VECTOR["audience"] + "/")  # a trailing slash does not count
    assert protocol.key_hash(e["key"]).hex() == e["key_hash_hex"]
    message = protocol.enroll_message(e["key"], aud)
    assert message.decode() == e["message"]
    assert protocol.b64(_private().sign(message)) == e["proof_b64"]


def test_challenge_signature_matches_the_vector() -> None:
    a = VECTOR["auth"]
    message = protocol.auth_message(a["machine_id"], a["nonce_b64"], VECTOR["audience"])
    assert message.decode() == a["message"]
    assert protocol.b64(_private().sign(message)) == a["signature_b64"]
    # The nonce is signed as the text the server sent, not as the bytes it decodes to.
    assert base64.b64decode(a["nonce_b64"]) != a["nonce_b64"].encode()


def test_the_audience_separates_environments() -> None:
    a = VECTOR["auth"]
    here = protocol.auth_message(a["machine_id"], a["nonce_b64"], "https://producao.example")
    there = protocol.auth_message(a["machine_id"], a["nonce_b64"], "https://homologacao.example")
    assert here != there
