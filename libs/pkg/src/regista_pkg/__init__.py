"""The signed robot package format (ADR 0021), shared by the API, the agent and `regista-pack`.

One implementation of "what is signed" and "how it is verified", so the three sides cannot drift.
"""

from regista_pkg.keys import key_id_of, load_trusted_keys, parse_key_file
from regista_pkg.manifest import FORMAT, Manifest, SignedManifest, canonical_message
from regista_pkg.reasons import REASONS, PackageError
from regista_pkg.signature import (
    SIGNATURE_FORMAT,
    parse_signature_doc,
    sign,
    verify,
    verify_file_hash,
)

__all__ = [
    "FORMAT",
    "REASONS",
    "SIGNATURE_FORMAT",
    "Manifest",
    "PackageError",
    "SignedManifest",
    "canonical_message",
    "key_id_of",
    "load_trusted_keys",
    "parse_key_file",
    "parse_signature_doc",
    "sign",
    "verify",
    "verify_file_hash",
]
