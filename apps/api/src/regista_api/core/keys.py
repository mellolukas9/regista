"""KeyProvider: encryption of secrets at rest (docs/specs/security.md).

Dev uses a local master key; production will use AWS KMS with one key per tenant (M8).
"""

import base64
import hashlib
import hmac
import os
import uuid
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_BYTES = 12


class KeyProvider(Protocol):
    def encrypt(
        self, tenant_id: uuid.UUID, plaintext: bytes, *, aad: bytes = b""
    ) -> tuple[bytes, str]:
        """Return `(ciphertext, key_id)`."""
        ...

    def decrypt(
        self, tenant_id: uuid.UUID, ciphertext: bytes, key_id: str, *, aad: bytes = b""
    ) -> bytes: ...

    def mac(self, purpose: str, data: bytes) -> bytes:
        """HMAC-SHA256 of `data` under a key bound to `purpose` (e.g. the agent token)."""
        ...

    def verify_mac(self, purpose: str, data: bytes, mac: bytes) -> bool:
        """Constant-time check; a MAC made for another purpose never verifies."""
        ...


class KeyProviderError(Exception):
    pass


class LocalKeyProvider:
    """AES-GCM with a master key from `REGISTA_MASTER_KEY` (base64, 32 bytes).

    The tenant id is mixed into the associated data, so a ciphertext copied to another
    tenant's row does not decrypt.
    """

    key_id = "local-1"

    def __init__(self, master_key_b64: str) -> None:
        try:
            key = base64.b64decode(master_key_b64, validate=True)
        except ValueError as exc:
            raise KeyProviderError("REGISTA_MASTER_KEY is not valid base64") from exc
        if len(key) != 32:
            raise KeyProviderError("REGISTA_MASTER_KEY must decode to exactly 32 bytes")
        self._aes = AESGCM(key)
        self._master = key
        self._mac_keys: dict[str, bytes] = {}

    def _mac_key(self, purpose: str) -> bytes:
        """HKDF-SHA256 from the master key, one independent key per purpose."""
        derived = self._mac_keys.get(purpose)
        if derived is None:
            derived = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=b"regista/mac/v1/" + purpose.encode(),
            ).derive(self._master)
            self._mac_keys[purpose] = derived
        return derived

    def mac(self, purpose: str, data: bytes) -> bytes:
        return hmac.new(self._mac_key(purpose), data, hashlib.sha256).digest()

    def verify_mac(self, purpose: str, data: bytes, mac: bytes) -> bool:
        return hmac.compare_digest(self.mac(purpose, data), mac)

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(os.urandom(32)).decode()

    def _aad(self, tenant_id: uuid.UUID, aad: bytes) -> bytes:
        return tenant_id.bytes + aad

    def encrypt(
        self, tenant_id: uuid.UUID, plaintext: bytes, *, aad: bytes = b""
    ) -> tuple[bytes, str]:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aes.encrypt(nonce, plaintext, self._aad(tenant_id, aad))
        return nonce + sealed, self.key_id

    def decrypt(
        self, tenant_id: uuid.UUID, ciphertext: bytes, key_id: str, *, aad: bytes = b""
    ) -> bytes:
        if key_id != self.key_id:
            raise KeyProviderError(f"unknown key id: {key_id}")
        nonce, sealed = ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:]
        try:
            return self._aes.decrypt(nonce, sealed, self._aad(tenant_id, aad))
        except InvalidTag as exc:
            raise KeyProviderError("ciphertext failed authentication") from exc
