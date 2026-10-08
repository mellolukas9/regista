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
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
import truststore

from regista_agent import layout, policy, runtime, trust
from regista_agent import service as service_module
from regista_agent.config import AgentSettings
from regista_agent.errors import AgentError, IdentityRejected, ServerUnavailable
from regista_agent.jobapi import HttpJobApi, RuntimeNeed
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
    checks.extend(local_checks(settings, store))
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
    session = AgentSession(
        http,
        machine_id=str(settings.machine_id),
        private_key=store.load(),
        server_url=settings.server_url,
    )
    try:
        session.login()
    except IdentityRejected as exc:
        checks.append(Check("Login", "erro", str(exc)))
    except (AgentError, ServerUnavailable) as exc:
        checks.append(Check("Login", "erro", f"Não foi possível obter um token: {exc}"))
    else:
        checks.append(Check("Login", "ok", "O servidor aceitou a identidade desta máquina."))
        checks.append(runtime_check(settings, HttpJobApi(session)))
    return checks


def local_checks(settings: AgentSettings, store: KeyStore) -> list[Check]:
    """What this machine itself allows, none of it from the server: the keys it trusts, whose it
    is, which robots it may run, the kill switch and the runtimes installed (M4)."""
    checks: list[Check] = []
    try:
        keys = trust.trusted_keys(settings.environment)
    except AgentError as exc:
        checks.append(Check("Chaves de assinatura", "erro", str(exc)))
    else:
        dev = trust.dev_keys_file(settings.environment) is not None
        if not keys:
            checks.append(
                Check(
                    "Chaves de assinatura",
                    "erro",
                    "Nenhuma chave de assinatura confiável: nenhum pacote vai rodar.",
                )
            )
        else:
            detail = "Confia em " + ", ".join(sorted(keys)) + "."
            if dev:
                detail += " Há chaves extras de desenvolvimento (REGISTA_DEV_TRUSTED_KEYS)."
            checks.append(Check("Chaves de assinatura", "aviso" if dev else "ok", detail))

    try:
        tenant = store.read_tenant_id()
    except AgentError as exc:
        checks.append(Check("Cliente da máquina", "erro", str(exc)))
    else:
        if tenant is None:
            checks.append(
                Check(
                    "Cliente da máquina",
                    "aviso",
                    "Esta máquina foi cadastrada antes dos pacotes assinados e não guarda o "
                    "próprio cliente. Gere uma nova chave no painel e rode o cadastro de novo "
                    "(enroll).",
                )
            )
        else:
            checks.append(Check("Cliente da máquina", "ok", f"Pertence ao cliente {tenant}."))

    if settings.allowed_bots:
        checks.append(
            Check("Robôs permitidos", "ok", ", ".join(sorted(settings.allowed_bots)) + ".")
        )
    else:
        checks.append(
            Check(
                "Robôs permitidos",
                "aviso",
                "A lista está vazia: nenhum robô roda. Libere com `regista-agent allow <pacote>`.",
            )
        )
    if policy.is_paused(settings):
        checks.append(
            Check(
                "Kill switch",
                "aviso",
                f"Ligado ({policy.pause_path(settings)}): o agente não pega novas execuções. "
                "Retome com `regista-agent resume`.",
            )
        )
    else:
        checks.append(Check("Kill switch", "ok", "Desligado."))

    pythons, revisions = runtime.installed(settings)
    checks.append(
        Check(
            "Runtimes instalados",
            "ok" if pythons else "aviso",
            f"Python: {', '.join(pythons) or 'nenhum'}; "
            f"Chromium: {', '.join(revisions) or 'nenhum'}."
            + ("" if pythons else " Prepare com `regista-agent setup`."),
        )
    )
    checks.extend(runtime_acl_checks(settings))
    checks.extend(host_checks(settings))
    return checks


def runtime_acl_checks(settings: AgentSettings) -> list[Check]:
    """The permission matrix of the agent's folders (`layout.py`): nobody may have more, or less,
    than the table says, and local users get nothing."""
    if sys.platform != "win32":
        return []
    from regista_agent import _windows

    if not settings.agent_account:
        return []
    robot_account = settings.effective_robot_account
    if robot_account is None:
        return [Check("Permissões das pastas", "erro", "A conta do robô não está configurada.")]
    try:
        agent_sid = _windows.resolve_sid(settings.agent_account)
        robot_sid = _windows.resolve_sid(robot_account)
    except OSError as exc:
        return [Check("Permissões das pastas", "erro", str(exc))]
    problems = layout.check(settings, agent_sid, robot_sid)
    if problems:
        return [
            Check(
                "Permissões das pastas",
                "erro",
                " ".join(problems) + " Rode `regista-agent setup` como administrador.",
            )
        ]
    return [
        Check(
            "Permissões das pastas",
            "ok",
            "Cada pasta tem só o acesso previsto: o robô não alcança a chave, a configuração, "
            "os pacotes nem o cache; usuários comuns não alcançam nada.",
        )
    ]


def _service_user(name: str, run: "service_module.Run") -> tuple[str, str]:
    """(state, the account the service's process runs as) as the system reports them."""
    import psutil

    info = service_module.query(run, name)
    if not info.installed:
        return "", ""
    pid = None
    for line in (run(["sc.exe", "queryex", name]).stdout or "").splitlines():
        if "PID" in line and ":" in line:
            text = line.split(":", 1)[1].strip()
            pid = int(text) if text.isdigit() else None
    user = ""
    if pid:
        try:
            user = psutil.Process(pid).username()
        except (psutil.Error, OSError):
            user = ""
    return info.state, user


def host_checks(settings: AgentSettings, run: "service_module.Run | None" = None) -> list[Check]:
    """The agent and the robot host as the system really runs them (ADR 0022): the agent as a
    service with its own account, the host (service or logon task) with the robot's account, the
    pipe in place, the dedicated user not an administrator, and the program files unchangeable by
    anyone but administrators."""
    if sys.platform != "win32":
        return []
    from regista_agent import _windows
    from regista_agent.config import (
        AGENT_SERVICE_NAME,
        DEFAULT_SERVICE_ACCOUNT,
        ROBOT_HOST_PIPE,
        ROBOT_SERVICE_NAME,
    )

    runner = run or service_module._default_run
    checks: list[Check] = []

    def add(name: str, status: Status, detail: str) -> None:
        checks.append(Check(name, status, detail))

    expected_agent = settings.agent_account or DEFAULT_SERVICE_ACCOUNT
    state, user = _service_user(AGENT_SERVICE_NAME, runner)
    if not state:
        add(
            "Serviço do agente",
            "erro",
            f"O serviço {AGENT_SERVICE_NAME} não está instalado. Rode `regista-agent service "
            "install` como administrador.",
        )
    elif "RUNNING" not in state:
        add("Serviço do agente", "erro", f"O serviço {AGENT_SERVICE_NAME} está parado ({state}).")
    elif user and user.upper() != expected_agent.upper():
        add(
            "Serviço do agente",
            "erro",
            f"O agente roda como {user}, e deveria rodar como {expected_agent}.",
        )
    else:
        add(
            "Serviço do agente",
            "ok",
            f"{AGENT_SERVICE_NAME} rodando como {user or expected_agent}.",
        )

    robot = settings.effective_robot_account
    if settings.mode == "session":
        task = runner(["schtasks.exe", "/Query", "/TN", service_module.TASK_NAME])
        if task.returncode != 0:
            add(
                "Hospedeiro do robô",
                "erro",
                f"A tarefa de logon {service_module.TASK_NAME} não está instalada. Rode "
                "`regista-agent service install --mode session --robot-account <usuário>`.",
            )
        else:
            add(
                "Hospedeiro do robô",
                "ok",
                f"Tarefa de logon instalada para {robot or '?'}; o hospedeiro roda quando esse "
                "usuário está logado.",
            )
        if robot:
            try:
                if _windows.is_local_administrator(robot):
                    add(
                        "Usuário dedicado",
                        "erro",
                        f"{robot} é administrador desta máquina. Um robô com essa conta pode tudo, "
                        "inclusive ler a chave. Use um usuário comum.",
                    )
                else:
                    add("Usuário dedicado", "ok", f"{robot} não é administrador.")
            except OSError as exc:
                add("Usuário dedicado", "aviso", f"Não foi possível conferir: {exc}")
    else:
        state, user = _service_user(ROBOT_SERVICE_NAME, runner)
        expected_robot = robot or ""
        if not state:
            add(
                "Hospedeiro do robô",
                "erro",
                f"O serviço {ROBOT_SERVICE_NAME} não está instalado. Rode `regista-agent service "
                "install` como administrador.",
            )
        elif "RUNNING" not in state:
            add(
                "Hospedeiro do robô",
                "erro",
                f"O serviço {ROBOT_SERVICE_NAME} está parado ({state}).",
            )
        elif user and expected_robot and user.upper() != expected_robot.upper():
            add(
                "Hospedeiro do robô",
                "erro",
                f"O hospedeiro roda como {user}, e deveria rodar como {expected_robot}.",
            )
        elif user and user.upper() == expected_agent.upper():
            add("Hospedeiro do robô", "erro", "O hospedeiro roda com a conta do agente.")
        else:
            add(
                "Hospedeiro do robô",
                "ok",
                f"{ROBOT_SERVICE_NAME} rodando como {user or expected_robot}.",
            )

    if os.path.exists("\\\\.\\pipe\\" + ROBOT_HOST_PIPE):
        add(
            "Canal do hospedeiro",
            "ok",
            "O pipe do agente existe (instância única, só o robô abre).",
        )
    else:
        add(
            "Canal do hospedeiro",
            "aviso",
            "O pipe do agente não existe: o agente não está rodando, e as execuções vão falhar com "
            "robot_host_unavailable.",
        )

    problems = service_module.insecure_paths(Path(sys.executable).resolve())
    if problems:
        add(
            "Arquivos do programa",
            "aviso",
            "Podem ser alterados por contas que não são administradores: "
            + "; ".join(problems[:4])
            + ". Instale o programa em uma pasta só de administradores.",
        )
    else:
        add("Arquivos do programa", "ok", "Só administradores e SYSTEM alteram o programa.")
    return checks


def runtime_check(settings: AgentSettings, api: HttpJobApi) -> Check:
    """Does this machine have what the versions in use in its pool ask for?"""
    try:
        needs: list[RuntimeNeed] = api.runtimes()
    except (AgentError, ServerUnavailable) as exc:
        return Check("Runtimes do pool", "aviso", f"Não foi possível consultar: {exc}")
    pythons, revisions = runtime.installed(settings)
    missing: list[str] = []
    for need in needs:
        if need.python not in pythons and runtime.python_executable(settings, need.python) is None:
            missing.append(f"{need.package_name} v{need.version} pede Python {need.python}")
        if need.chromium_revision and need.chromium_revision not in revisions:
            missing.append(
                f"{need.package_name} v{need.version} pede Chromium {need.chromium_revision}"
            )
    if missing:
        return Check(
            "Runtimes do pool",
            "erro",
            "Falta preparar esta máquina: " + "; ".join(missing) + ". Rode `regista-agent setup "
            "--from-server` como administrador.",
        )
    return Check("Runtimes do pool", "ok", "Tudo o que as versões em uso pedem está instalado.")


def format_checks(checks: list[Check]) -> str:
    labels = {"ok": "OK   ", "aviso": "AVISO", "erro": "ERRO "}
    return "\n".join(f"[{labels[c.status]}] {c.name}: {c.detail}" for c in checks)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "erro" for c in checks) else 0
