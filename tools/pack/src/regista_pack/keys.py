"""The signing key: generated here, kept encrypted, never inside a repository.

The private key is a PKCS8 file encrypted with a passphrase. The passphrase is asked on the
terminal; `REGISTA_SIGNING_PASSPHRASE` exists only for use on the build machine itself. The
production key never goes to a CI or to the secrets of a repository host (ADR 0021): CI makes
throwaway keys of its own.
"""

import base64
import getpass
import json
import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_pkg import key_id_of

ENV_PASSPHRASE = "REGISTA_SIGNING_PASSPHRASE"  # noqa: S105  (the name of a variable, not a secret)


class PackError(Exception):
    """Something the person running regista-pack can fix; the message says how."""


def _inside_a_repository(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in [path.resolve(), *path.resolve().parents])


def passphrase(*, confirm: bool, env: Mapping[str, str] | None = None) -> bytes:
    value = (os.environ if env is None else env).get(ENV_PASSPHRASE)
    if value is None:
        value = getpass.getpass("Senha da chave de assinatura: ")
        if confirm and getpass.getpass("Repita a senha: ") != value:
            raise PackError("As senhas não são iguais.")
    if len(value) < 12:
        raise PackError("A senha da chave precisa ter pelo menos 12 caracteres.")
    return value.encode("utf-8")


def public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def generate(out_dir: Path, name: str, password: bytes) -> tuple[Path, Path, str]:
    """Write `<name>.rgkey` (private, encrypted) and `<name>.pub.json` (public). Returns both
    paths and the key id. Refuses a folder inside a repository and never overwrites."""
    if _inside_a_repository(out_dir):
        raise PackError(
            f"{out_dir} está dentro de um repositório git. A chave de assinatura nunca fica no "
            "repositório: use uma pasta fora dele (por exemplo, em %USERPROFILE%\\.regista)."
        )
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise PackError("Use só letras, números, - e _ no nome da chave.")
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path, public_path = out_dir / f"{name}.rgkey", out_dir / f"{name}.pub.json"
    for path in (private_path, public_path):
        if path.exists():
            raise PackError(f"{path} já existe. Não vou sobrescrever uma chave.")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    key_id = key_id_of(public_raw(key))
    with private_path.open("xb") as handle:
        handle.write(pem)
    try:
        os.chmod(private_path, 0o600)
    except OSError:
        pass
    public_path.write_text(
        json.dumps({key_id: base64.b64encode(public_raw(key)).decode("ascii")}, indent=2) + "\n",
        encoding="utf-8",
    )
    return private_path, public_path, key_id


def load_private(path: Path, password: bytes) -> tuple[Ed25519PrivateKey, str]:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password)
    except FileNotFoundError:
        raise PackError(f"Não encontrei a chave {path}.") from None
    except (ValueError, TypeError):
        raise PackError("Senha errada ou arquivo de chave inválido.") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise PackError("A chave de assinatura precisa ser Ed25519.")
    return key, key_id_of(public_raw(key))
