"""The public keys every agent and the API trust (ADR 0021): the active key and the reserve key.

`key_id -> base64 of the raw 32-byte Ed25519 public key`. A public key is not a secret; changing
this list is a reviewed pull request, and it goes into the agent's wheel and, in M8, its binary.

It is empty until the production keys exist (docs/runbooks/chave-de-assinatura.md): with no key
nothing is trusted, so no package runs. That is the safe way to be empty. The two production keys
must be generated and listed here before the first customer.
"""

TRUSTED_PUBLIC_KEYS: dict[str, str] = {}
