"""Where the machine's private key lives (docs/adr/0018).

The key is generated on the machine and never leaves it. At rest:

- **Windows:** the 32 raw bytes go through DPAPI with the *machine* scope and a fixed extra
  entropy, and the folder's ACL allows only the account that runs the agent, SYSTEM and
  Administrators (inheritance off). Accepted limit: an administrator of the machine can read the
  key and use it. Moving it to the TPM is the evolution (a platform key provider); the rest of the
  agent does not change, because it only calls `load`.
- **Linux:** a 0600 file in a 0700 folder owned by the agent's account.

The account that runs `enroll` (usually an administrator in a console) is **not** the one that runs
the service afterwards, so access is granted to `agent_account`, never to the person at the
console. `check_acl` is what `diagnose` uses to prove the folder is as strict as it should be.
"""

import getpass
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent.errors import AgentError, NotEnrolled

KEY_ENTROPY = b"regista-agent/machine-key/v1"
KEY_NAME = "machine.key"
STAGED_NAME = "machine.key.new"
_RAW_KEY_SIZE = 32


@dataclass(frozen=True)
class AclReport:
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def _raw(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )


def _protect(raw: bytes) -> bytes:
    if sys.platform == "win32":
        from regista_agent import _windows

        return _windows.protect(raw, KEY_ENTROPY)
    return raw


def _unprotect(blob: bytes) -> bytes:
    if sys.platform == "win32":
        from regista_agent import _windows

        return _windows.unprotect(blob, KEY_ENTROPY)
    return blob


class KeyStore:
    def __init__(self, keys_dir: Path, *, agent_account: str | None) -> None:
        self.keys_dir = keys_dir
        self.agent_account = agent_account
        self.path = keys_dir / KEY_NAME
        self.staged_path = keys_dir / STAGED_NAME

    def exists(self) -> bool:
        return self.path.is_file()

    # --- writing ------------------------------------------------------------------------------

    def prepare(self) -> None:
        """Create the folder and lock it down. Nothing the key could leak through exists yet."""
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            from regista_agent import _windows

            if not self.agent_account:
                raise AgentError("Informe a conta que roda o agente (--agent-account).")
            try:
                _windows.restrict_directory(self.keys_dir, _windows.resolve_sid(self.agent_account))
            except OSError as exc:
                raise AgentError(f"Não foi possível proteger a pasta da chave: {exc}") from exc
        else:
            os.chmod(self.keys_dir, 0o700)
            self._give_to_agent(self.keys_dir)

    def stage(self, key: Ed25519PrivateKey) -> Path:
        """Write the key next to its final place, protected, without replacing anything yet.

        Enrollment stages first and commits only when the server accepted the key, so a refused
        or interrupted enrollment never destroys an identity that already works.
        """
        self.prepare()
        self.staged_path.unlink(missing_ok=True)
        data = _protect(_raw(key))
        if sys.platform == "win32":
            from regista_agent import _windows

            _windows.make_private_file(self.staged_path, data)
        else:
            descriptor = os.open(self.staged_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._give_to_agent(self.staged_path)
        return self.staged_path

    def commit(self, staged: Path) -> None:
        os.replace(staged, self.path)

    def discard(self, staged: Path) -> None:
        staged.unlink(missing_ok=True)

    def _give_to_agent(self, path: Path) -> None:
        if not self.agent_account or self.agent_account == getpass.getuser():
            return
        try:
            shutil.chown(path, user=self.agent_account)
        except (OSError, LookupError) as exc:
            raise AgentError(
                f"Não foi possível entregar a chave à conta {self.agent_account}: {exc}"
            ) from exc

    # --- reading ------------------------------------------------------------------------------

    def load(self) -> Ed25519PrivateKey:
        if not self.exists():
            raise NotEnrolled("Esta máquina ainda não foi cadastrada. Rode `regista-agent enroll`.")
        try:
            raw = _unprotect(self.path.read_bytes())
        except OSError as exc:
            raise AgentError(f"Não foi possível ler a chave da máquina: {exc}") from exc
        if len(raw) != _RAW_KEY_SIZE:
            raise AgentError("O arquivo da chave da máquina está corrompido.")
        return Ed25519PrivateKey.from_private_bytes(raw)

    # --- checking -----------------------------------------------------------------------------

    def check_acl(self) -> AclReport:
        """Is the key as protected as `prepare` leaves it? An empty list of problems means yes."""
        if sys.platform == "win32":
            return self._check_windows_acl()
        return self._check_posix_permissions()

    def _check_posix_permissions(self) -> AclReport:
        problems: list[str] = []
        for path, label in ((self.keys_dir, "A pasta da chave"), (self.path, "O arquivo da chave")):
            if not path.exists():
                problems.append(f"{label} não existe: {path}")
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                problems.append(
                    f"{label} está aberto demais ({oct(mode)}); deveria ser só do dono."
                )
        return AclReport(problems)

    def _check_windows_acl(self) -> AclReport:
        from regista_agent import _windows

        problems: list[str] = []
        if not self.agent_account:
            return AclReport(["A conta do agente não está configurada (agent_account)."])
        try:
            agent_sid = _windows.resolve_sid(self.agent_account)
        except OSError as exc:
            return AclReport([str(exc)])
        allowed = {_windows.SYSTEM_SID, _windows.ADMINISTRATORS_SID, agent_sid}

        targets = [(self.keys_dir, "a pasta da chave")]
        if self.path.exists():
            targets.append((self.path, "o arquivo da chave"))
        else:
            problems.append(f"O arquivo da chave não existe: {self.path}")
        agent_can_read = False
        for path, label in targets:
            try:
                dacl = _windows.read_dacl(path)
            except OSError as exc:
                problems.append(str(exc))
                continue
            if path == self.keys_dir and not dacl.protected:
                problems.append("A pasta da chave herda permissões da pasta de cima.")
            for ace in dacl.aces:
                if ace.kind == "A" and ace.sid not in allowed:
                    problems.append(f"Uma conta fora da lista tem acesso a {label}: {ace.sid}.")
                if ace.kind == "D" and ace.sid == agent_sid:
                    problems.append(f"A conta do agente está bloqueada em {label}.")
                if path == self.path and ace.kind == "A" and ace.sid == agent_sid and ace.can_read:
                    agent_can_read = True
        if self.path.exists() and not agent_can_read:
            problems.append(f"A conta do agente ({self.agent_account}) não consegue ler a chave.")
        return AclReport(problems)
