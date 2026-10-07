"""Which public keys this agent trusts to sign robot packages (ADR 0021).

The list is compiled in (`regista_pkg.trusted_keys`). The only way to add a key is the development
override `REGISTA_DEV_TRUSTED_KEYS`, a file of extra public keys, and it exists **only** for a
development agent:

* it is read from the process environment and never from `agent.toml`, so it is not a setting a
  file can carry;
* in production the agent does not start with it set (it refuses instead of ignoring it), so a key
  can never be added quietly.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from regista_agent.errors import AgentError
from regista_pkg import load_trusted_keys, parse_key_file

ENV_DEV_TRUSTED_KEYS = "REGISTA_DEV_TRUSTED_KEYS"


def dev_keys_file(environment: str, env: Mapping[str, str] | None = None) -> Path | None:
    """The override file, or None. Raises in production if the variable is set at all."""
    value = (os.environ if env is None else env).get(ENV_DEV_TRUSTED_KEYS, "").strip()
    if not value:
        return None
    if environment != "dev":
        raise AgentError(
            f"{ENV_DEV_TRUSTED_KEYS} só pode ser usado com REGISTA_ENVIRONMENT=dev. Em produção "
            "o agente só confia nas chaves embutidas nele."
        )
    return Path(value)


def trusted_keys(
    environment: str, env: Mapping[str, str] | None = None
) -> dict[str, Ed25519PublicKey]:
    """The compiled-in keys, plus the development override when it applies."""
    path = dev_keys_file(environment, env)
    extra: dict[str, Ed25519PublicKey] = {}
    if path is not None:
        try:
            extra = parse_key_file(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise AgentError(f"{ENV_DEV_TRUSTED_KEYS}: {exc}") from exc
    return load_trusted_keys(extra)
