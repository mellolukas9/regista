"""`regista-agent enroll`: give this machine its identity (docs/specs/agent.md).

The key pair is generated here and the private key never leaves; the server only gets the public
half and a signature proving the machine holds the private one. Nothing is changed on the machine
until the server has accepted it: the key is written next to its final place first (which also
proves the folder can be locked down), and only then is it put in place.
"""

import sys
import uuid
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent import protocol, sysinfo
from regista_agent.config import (
    DEFAULT_ROBOT_ACCOUNT,
    DEFAULT_SERVICE_ACCOUNT,
    AgentSettings,
    save_identity,
)
from regista_agent.errors import AgentError, EnrollmentRefused
from regista_agent.keystore import KeyStore
from regista_agent.transport import HttpSession, error_code, make_client

ALREADY_ENROLLED = (
    "Esta máquina já tem uma identidade cadastrada. Cadastrar de novo substitui a chave atual, e "
    "o agente em execução deixa de funcionar assim que a nova chave for usada.\n"
    "Se esta máquina é uma cópia (VM clonada), o cadastro deve ser feito depois da clonagem, em "
    "uma imagem que nunca teve o agente cadastrado (no Windows, rode o sysprep antes de clonar).\n"
    "Para substituir mesmo assim, use --force."
)
REFUSED = (
    "O servidor recusou a chave de registro. Confira se a chave está certa, se ainda não "
    "expirou (vale 24 horas) e se ainda não foi usada. A chave também precisa ter sido gerada "
    "para esta máquina, e a URL deve ser exatamente a do servidor, como no painel."
)
SESSION_NEEDS_ACCOUNT = (
    "Esta máquina foi cadastrada no modo Sessão, e o modo Sessão exige a conta do usuário "
    "dedicado em que o hospedeiro e os robôs rodam. A chave de registro já foi usada, então a "
    "identidade não foi gravada. Gere uma nova chave no painel e rode o cadastro de novo com "
    "--robot-account <usuário>."
)


@dataclass(frozen=True)
class EnrollResult:
    machine_id: uuid.UUID
    mode: str
    heartbeat_seconds: int
    config_path: Path
    agent_account: str | None
    robot_account: str | None = None


def normalize_url(url: str) -> str:
    """The server URL as the agent signs it. Plain http is only for this computer (development)."""
    parsed = urlparse(url.strip())
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or not host:
        raise AgentError(
            "A URL do servidor precisa começar com https:// (ex.: https://regista.suaempresa.com.br)."
        )
    if parsed.scheme == "http" and not _is_loopback(host):
        raise AgentError(
            "A conexão com o servidor precisa usar https://. O http só é aceito para este "
            "computador (desenvolvimento)."
        )
    return protocol.audience(url.strip())


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def enroll(
    settings: AgentSettings,
    *,
    url: str,
    key: str,
    agent_account: str | None,
    robot_account: str | None = None,
    force: bool = False,
    http: HttpSession | None = None,
) -> EnrollResult:
    server_url = normalize_url(url)
    account = agent_account or (DEFAULT_SERVICE_ACCOUNT if sys.platform == "win32" else None)
    store = KeyStore(settings.keys_dir, agent_account=account)
    if (settings.enrolled or store.exists()) and not force:
        raise AgentError(ALREADY_ENROLLED)

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    proof = private.sign(protocol.enroll_message(key, protocol.audience(server_url)))

    # Locks the folder down and writes the key beside its final place. If that cannot be done
    # (not an administrator, unknown account) we find out now, before the key is used up.
    staged = store.stage(private)
    staged_identity: Path | None = None
    try:
        session = http or HttpSession(
            make_client(settings.model_copy(update={"server_url": server_url}))
        )
        response = session.send(
            "POST",
            "/agent/enroll",
            json={
                "key": key,
                "public_key": protocol.b64(public),
                "proof": protocol.b64(proof),
                "agent_version": sysinfo.agent_version(),
                "os_info": sysinfo.collect(),
            },
            attempts=3,
        )
        if response.status_code == 401 and error_code(response) == "invalid_enrollment_key":
            raise EnrollmentRefused(REFUSED)
        if response.status_code != 200:
            raise AgentError(f"O servidor não aceitou o cadastro (HTTP {response.status_code}).")
        answer = response.json()
        mode = str(answer["mode"])
        tenant_id = uuid.UUID(str(answer["tenant_id"]))
        staged_identity = store.stage_identity(tenant_id)
        if mode == "session" and robot_account is None and sys.platform == "win32":
            raise AgentError(SESSION_NEEDS_ACCOUNT)
    except BaseException:
        store.discard(*(p for p in (staged, staged_identity) if p is not None))
        raise

    store.commit(staged)
    store.commit_identity(staged_identity)
    machine_id = uuid.UUID(str(answer["machine_id"]))
    # In `service` mode the robot runs as its own virtual account (ADR 0022); in `session`
    # mode as the dedicated user the person named. Never as the agent.
    robot = robot_account or (
        DEFAULT_ROBOT_ACCOUNT if sys.platform == "win32" and mode == "service" else None
    )
    config_path = save_identity(
        settings.home,
        server_url=server_url,
        machine_id=machine_id,
        mode=mode,
        agent_account=account,
        robot_account=robot,
    )
    return EnrollResult(
        machine_id=machine_id,
        mode=mode,
        heartbeat_seconds=int(answer["heartbeat_seconds"]),
        config_path=config_path,
        agent_account=account,
        robot_account=robot,
    )
