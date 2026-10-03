"""`regista-agent diagnose`: why can this machine (not) talk to the server?

Each check says ok, aviso or erro in plain Portuguese, in the order a person would investigate:
the machine, then the network (DNS, TCP, proxy, TLS), then the server, the clock, the key's
permissions and, last, a real login. Anything that touches the system goes through `Probes`, so
tests can play out a broken network. The exit code is not zero when there is at least one erro.
"""

import os
import shutil
import socket
import ssl
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urlparse

import httpx
import truststore

from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError, IdentityRejected, ServerUnavailable
from regista_agent.keystore import KeyStore
from regista_agent.transport import AgentSession, HttpSession, make_client

Status = Literal["ok", "aviso", "erro"]
CLOCK_TOLERANCE_SECONDS = 60
MIN_PYTHON = (3, 12)


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class CertInfo:
    subject: str
    issuer: str
    not_after: str


def _resolve(host: str, port: int) -> list[str]:
    return sorted(
        {str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )


def _connect(host: str, port: int) -> None:
    socket.create_connection((host, port), timeout=5).close()


def _peer_certificate(host: str, port: int, settings: AgentSettings) -> CertInfo:
    context: ssl.SSLContext = (
        ssl.create_default_context(cafile=str(settings.ca_bundle))
        if settings.ca_bundle
        else truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )
    with (
        socket.create_connection((host, port), timeout=8) as raw,
        context.wrap_socket(raw, server_hostname=host) as tls,
    ):
        cert = tls.getpeercert() or {}

    def name(parts: object) -> str:
        pairs = dict(item[0] for item in parts) if isinstance(parts, tuple) else {}
        return str(pairs.get("commonName") or pairs.get("organizationName") or "?")

    return CertInfo(
        name(cert.get("subject")), name(cert.get("issuer")), str(cert.get("notAfter", "?"))
    )


@dataclass
class Probes:
    """Everything that touches the system, replaceable in tests."""

    resolve: Callable[[str, int], list[str]] = _resolve
    connect: Callable[[str, int], None] = _connect
    peer_certificate: Callable[[str, int, AgentSettings], CertInfo] = _peer_certificate
    which: Callable[[str], str | None] = shutil.which
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    client: Callable[[AgentSettings], httpx.Client] = make_client
    python: tuple[int, int, int] = field(default=tuple(sys.version_info[:3]))  # type: ignore[assignment]


def _proxy_in_use(settings: AgentSettings) -> str | None:
    if settings.proxy:
        return settings.proxy
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        if os.environ.get(name):
            return os.environ[name]
    return None


def run_checks(settings: AgentSettings, probes: Probes | None = None) -> list[Check]:
    probes = probes or Probes()
    checks: list[Check] = []

    def add(name: str, status: Status, detail: str) -> None:
        checks.append(Check(name, status, detail))

    # --- this machine ---------------------------------------------------------------------
    major, minor, micro = probes.python
    version = f"{major}.{minor}.{micro}"
    if (major, minor) >= MIN_PYTHON:
        add("Python", "ok", f"Python {version}.")
    else:
        add("Python", "erro", f"Python {version}: o agente precisa do Python 3.12 ou mais novo.")
    uv = probes.which("uv")
    if uv:
        add("uv", "ok", f"Encontrado em {uv}.")
    else:
        add(
            "uv",
            "aviso",
            "O uv não foi encontrado. Ele será necessário para preparar o ambiente dos robôs.",
        )

    if not settings.enrolled:
        add(
            "Cadastro",
            "erro",
            "Esta máquina ainda não foi cadastrada. Rode `regista-agent enroll`.",
        )
    else:
        add(
            "Cadastro",
            "ok",
            f"Máquina {settings.machine_id}, modo {settings.mode}, servidor {settings.server_url}.",
        )

    if settings.server_url is None:
        add("Rede", "aviso", "Sem URL do servidor, as verificações de rede foram puladas.")
        return _finish_with_key(settings, checks)

    # --- the network ----------------------------------------------------------------------
    parsed = urlparse(settings.server_url)
    host = parsed.hostname or ""
    secure = parsed.scheme == "https"
    port = parsed.port or (443 if secure else 80)
    proxy = _proxy_in_use(settings)

    if proxy:
        add("Proxy", "ok", f"Usando o proxy {proxy}.")
        proxy_parsed = urlparse(proxy)
        add(
            "DNS",
            "aviso",
            "Com proxy, quem resolve o endereço do servidor é o proxy; não testado aqui.",
        )
        try:
            probes.connect(proxy_parsed.hostname or "", proxy_parsed.port or 3128)
            add("Conexão TCP", "ok", f"O proxy {proxy_parsed.hostname} aceitou a conexão.")
        except OSError as exc:
            add("Conexão TCP", "erro", f"Não foi possível conectar ao proxy {proxy}: {exc}.")
    else:
        add("Proxy", "ok", "Nenhum proxy configurado (conexão direta).")
        try:
            addresses = probes.resolve(host, port)
            add("DNS", "ok", f"{host} resolve para {', '.join(addresses)}.")
        except OSError as exc:
            add("DNS", "erro", f"Não foi possível resolver {host}: {exc}.")
        try:
            probes.connect(host, port)
            add("Conexão TCP", "ok", f"Conectou em {host}:{port}.")
        except OSError as exc:
            add("Conexão TCP", "erro", f"Não foi possível conectar em {host}:{port}: {exc}.")

    if not secure:
        add("TLS", "aviso", "A conexão não usa TLS (http). Isso só é aceitável em desenvolvimento.")
    elif proxy:
        add(
            "TLS",
            "aviso",
            "Com proxy, o certificado é visto pela chamada ao servidor, logo abaixo.",
        )
    else:
        try:
            cert = probes.peer_certificate(host, port, settings)
            add(
                "TLS",
                "ok",
                f"Certificado de {cert.subject}, emitido por {cert.issuer}, "
                f"válido até {cert.not_after}.",
            )
        except ssl.SSLCertVerificationError as exc:
            add(
                "TLS",
                "erro",
                "O certificado do servidor não é confiável "
                f"({getattr(exc, 'verify_message', None) or exc}). Se a empresa "
                "usa um proxy que inspeciona TLS, instale a CA da empresa no Windows ou aponte "
                "REGISTA_CA_BUNDLE para ela.",
            )
        except (OSError, ssl.SSLError) as exc:
            add("TLS", "erro", f"Falha no handshake TLS com {host}:{port}: {exc}.")

    # --- the server -----------------------------------------------------------------------
    client = probes.client(settings)
    # One try per call: a diagnosis says what happens now, it does not wait for it to improve.
    http = HttpSession(client, attempts=1)
    health: httpx.Response | None = None
    try:
        health = client.get("/health", timeout=10)
    except httpx.HTTPError as exc:
        add("Servidor", "erro", f"A chamada a /health falhou: {type(exc).__name__}: {exc}.")
    if health is not None:
        if health.status_code == 200:
            add("Servidor", "ok", "O servidor respondeu /health.")
        else:
            add("Servidor", "erro", f"O /health respondeu HTTP {health.status_code}.")

    date_header = health.headers.get("date") if health is not None else None
    if date_header:
        try:
            skew = abs((parsedate_to_datetime(date_header) - probes.now()).total_seconds())
        except (TypeError, ValueError):
            add("Horário", "aviso", "Não foi possível ler o horário do servidor.")
        else:
            if skew > CLOCK_TOLERANCE_SECONDS:
                add(
                    "Horário",
                    "aviso",
                    f"O relógio desta máquina difere {int(skew)} s do servidor. Ajuste a hora "
                    "do Windows (certificados TLS e registros dependem disso).",
                )
            else:
                add("Horário", "ok", f"Diferença de {int(skew)} s em relação ao servidor.")
    else:
        add("Horário", "aviso", "O servidor não informou o horário; não foi possível comparar.")

    checks = _finish_with_key(settings, checks, http=http)
    client.close()
    return checks


def _finish_with_key(
    settings: AgentSettings, checks: list[Check], *, http: HttpSession | None = None
) -> list[Check]:
    """The key's permissions and a real login: the last two questions."""
    store = KeyStore(settings.keys_dir, agent_account=settings.agent_account)
    if not store.exists():
        checks.append(Check("Chave da máquina", "aviso", "Ainda não há chave nesta máquina."))
        return checks
    report = store.check_acl()
    if report.ok:
        checks.append(
            Check(
                "Chave da máquina",
                "ok",
                "Só a conta do agente, o SYSTEM e os administradores têm acesso.",
            )
        )
    else:
        checks.append(Check("Chave da máquina", "erro", " ".join(report.problems)))

    if (
        http is None
        or not settings.enrolled
        or settings.machine_id is None
        or settings.server_url is None
    ):
        return checks
    try:
        AgentSession(
            http,
            machine_id=str(settings.machine_id),
            private_key=store.load(),
            server_url=settings.server_url,
        ).login()
    except IdentityRejected as exc:
        checks.append(Check("Login", "erro", str(exc)))
    except (AgentError, ServerUnavailable) as exc:
        checks.append(Check("Login", "erro", f"Não foi possível obter um token: {exc}"))
    else:
        checks.append(Check("Login", "ok", "O servidor aceitou a identidade desta máquina."))
    return checks


def format_checks(checks: list[Check]) -> str:
    labels = {"ok": "OK   ", "aviso": "AVISO", "erro": "ERRO "}
    return "\n".join(f"[{labels[c.status]}] {c.name}: {c.detail}" for c in checks)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "erro" for c in checks) else 0
