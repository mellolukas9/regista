"""`regista-agent`: enroll, run and diagnose (docs/specs/agent.md).

Exit codes: 0 ok, 1 something went wrong, 2 wrong usage, 3 the machine was revoked.
"""

import argparse
import logging
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from regista_agent import diagnose as diagnose_module
from regista_agent import enroll as enroll_module
from regista_agent import logs, loop, policy, runtime, sysinfo
from regista_agent.config import AgentSettings
from regista_agent.errors import EXIT_ERROR, AgentError, MachineRevoked


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regista-agent",
        description="Agente Regista: liga esta máquina ao painel. "
        "Só faz conexões de saída (HTTPS).",
    )
    parser.add_argument("--version", action="version", version=sysinfo.agent_version())
    commands = parser.add_subparsers(dest="command", required=True)

    enroll = commands.add_parser("enroll", help="Cadastra esta máquina com a chave de registro.")
    enroll.add_argument("--url", required=True, help="URL do servidor, como no painel.")
    enroll.add_argument("--key", required=True, help="Chave de registro (vale uma vez, 24 horas).")
    enroll.add_argument(
        "--agent-account",
        help="Conta que roda o agente e que poderá ler a chave. No Windows é sempre "
        "NT SERVICE\\RegistaAgent (o padrão); só muda em testes.",
    )
    enroll.add_argument(
        "--robot-account",
        help="Conta em que o hospedeiro e os robôs rodam. No modo Serviço é "
        "NT SERVICE\\RegistaRobot (o padrão); no modo Sessão, informe o usuário dedicado.",
    )
    enroll.add_argument("--force", action="store_true", help="Substitui uma identidade existente.")

    run = commands.add_parser("run", help="Roda o agente até Ctrl+C.")
    run.add_argument(
        "--mode",
        choices=("service", "session"),
        help="Confirma o modo da máquina. O modo vem do cadastro; "
        "se divergir, o agente não inicia.",
    )

    commands.add_parser("diagnose", help="Verifica por que a máquina fala (ou não) com o servidor.")

    host = commands.add_parser(
        "host", help="Hospedeiro do robô (iniciado pelo serviço ou pela tarefa de logon)."
    )
    host.add_argument("--mode", choices=("service", "session"), default="service")

    service = commands.add_parser(
        "service", help="Instala, remove ou mostra os serviços do Regista (console elevado)."
    )
    actions = service.add_subparsers(dest="service_command", required=True)
    install = actions.add_parser("install", help="Cria os serviços (ou a tarefa de logon).")
    install.add_argument("--mode", choices=("service", "session"), help="Padrão: o do cadastro.")
    install.add_argument("--robot-account", help="Usuário dedicado (modo Sessão).")
    install.add_argument(
        "--allow-insecure-path",
        action="store_true",
        help="Aceita instalar de uma pasta que não é só de administradores (desenvolvimento).",
    )
    install.add_argument("--start", action="store_true", help="Inicia os serviços ao final.")
    actions.add_parser("uninstall", help="Remove os serviços e a tarefa de logon.")
    actions.add_parser("status", help="Mostra os serviços, as contas e a segurança do caminho.")
    run_service = actions.add_parser("run", help="Usado pelo gerenciador de serviços.")
    run_service.add_argument("which", choices=("agent", "host"))

    setup = commands.add_parser(
        "setup",
        help="Prepara a máquina para os robôs: Python e Chromium exatos (console elevado).",
    )
    setup.add_argument(
        "--python", action="append", default=[], help="Python exato (X.Y.Z); repetível."
    )
    setup.add_argument("--playwright", help="Versão do Playwright, para instalar o Chromium dela.")
    setup.add_argument(
        "--chromium-revision", help="Revisão do Chromium que ela traz (conferência)."
    )
    setup.add_argument(
        "--wheels",
        help="Pasta com wheels (as de um pacote) para instalar o Playwright sem acessar o PyPI.",
    )
    setup.add_argument(
        "--from-server",
        action="store_true",
        help="Lê do servidor o que as versões em uso dos robôs deste pool pedem.",
    )

    for name, text in (
        ("allow", "Libera um robô nesta máquina (console elevado)."),
        ("disallow", "Tira um robô da lista desta máquina (console elevado)."),
    ):
        entry = commands.add_parser(name, help=text)
        entry.add_argument("package", help="Nome do pacote do robô.")
    commands.add_parser(
        "pause", help="Kill switch: para de pegar novas execuções (console elevado)."
    )
    commands.add_parser("resume", help="Desliga o kill switch (console elevado).")
    return parser


def _enroll(settings: AgentSettings, args: argparse.Namespace) -> int:
    result = enroll_module.enroll(
        settings,
        url=args.url,
        key=args.key,
        agent_account=args.agent_account,
        robot_account=args.robot_account,
        force=args.force,
    )
    print(f"Máquina cadastrada: {result.machine_id} (modo {result.mode}).")
    print(f"Configuração gravada em {result.config_path}.")
    if result.agent_account:
        print(f"A chave só pode ser lida por {result.agent_account}, SYSTEM e Administradores.")
    if result.robot_account:
        print(f"Os robôs rodam como {result.robot_account}, sem acesso à chave.")
    print(
        "Agora prepare a máquina (`regista-agent setup`) e instale os serviços "
        "(`regista-agent service install`)."
    )
    return 0


def _run(settings: AgentSettings, args: argparse.Namespace) -> int:
    logs.configure(settings)
    stop = threading.Event()
    try:
        loop.run(settings, expected_mode=args.mode, stop=stop)
    except KeyboardInterrupt:
        stop.set()
        print("Agente encerrado.")
    return 0


def _host(args: argparse.Namespace) -> int:
    from regista_agent import host as host_module
    from regista_agent.modes import session as session_mode

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.mode == "session":
        session_mode.preflight()
    stop = threading.Event()
    try:
        host_module.run_host(stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


def _service(settings: AgentSettings, args: argparse.Namespace) -> int:
    from regista_agent import service as service_module

    command = args.service_command
    if command == "install":
        mode = args.mode or settings.mode or "service"
        service_module.install(
            settings,
            mode=mode,
            robot_account=args.robot_account or settings.robot_account,
            allow_insecure_path=args.allow_insecure_path,
            start=args.start,
        )
        print("Pronto. Confira com `regista-agent service status` e `regista-agent diagnose`.")
    elif command == "uninstall":
        service_module.uninstall(settings)
    else:
        service_module.status(settings)
    return 0


def _service_run(args: argparse.Namespace) -> int:
    """The body of a Windows service: hands this process to the service manager."""
    if sys.platform != "win32":
        raise AgentError("Os serviços só existem no Windows.")
    from regista_agent import host as host_module
    from regista_agent import winservice

    log = logging.getLogger("regista_agent")

    def agent_work(stop: threading.Event) -> None:
        settings = AgentSettings()
        logs.configure(settings)
        try:
            loop.run(settings, stop=stop)
        except MachineRevoked as exc:
            log.error("%s", exc)  # a revoked machine stops for good, it is not restarted

    def host_work(stop: threading.Event) -> None:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
        host_module.run_host(stop)

    name = "RegistaAgent" if args.which == "agent" else "RegistaRobot"
    code = winservice.run_service(name, agent_work if args.which == "agent" else host_work)
    if code:
        raise AgentError(
            f"Este comando é do gerenciador de serviços (erro {code}); para instalar use "
            "`regista-agent service install`."
        )
    return 0


def _diagnose(settings: AgentSettings) -> int:
    checks = diagnose_module.run_checks(settings)
    print(diagnose_module.format_checks(checks))
    code = diagnose_module.exit_code(checks)
    print("\nTudo certo." if code == 0 and all(c.status == "ok" for c in checks) else "")
    return code


def _setup(settings: AgentSettings, args: argparse.Namespace) -> int:
    policy.require_elevation(settings, "O setup")
    needs: list[runtime.Needs] = []
    agent_sid: str | None = None
    robot_sid: str | None = None
    if sys.platform == "win32":
        from regista_agent import _windows

        if settings.agent_account:
            agent_sid = _windows.resolve_sid(settings.agent_account)
        robot_account = settings.effective_robot_account
        if robot_account:
            robot_sid = _windows.resolve_sid(robot_account)
    if args.from_server:
        from regista_agent.jobapi import HttpJobApi
        from regista_agent.keystore import KeyStore

        store = KeyStore(settings.keys_dir, agent_account=settings.agent_account)
        needs.extend(HttpJobApi(loop.build_session(settings, store)).runtimes())
    for version in args.python:
        needs.append(runtime.SimpleNeed(version, args.playwright, args.chromium_revision))
    if not needs:
        raise AgentError(
            "Diga o que preparar: --from-server, ou --python X.Y.Z (e --playwright X.Y.Z para o "
            "Chromium)."
        )
    wheels = Path(args.wheels) if args.wheels else None
    chosen = runtime.run_setup(
        settings, needs, agent_sid=agent_sid, robot_sid=robot_sid, wheels=wheels
    )
    print("Máquina preparada: Python " + ", ".join(chosen.pythons or ("nenhum",)) + ".")
    print(
        "Libere os robôs com `regista-agent allow <pacote>` e confira com `regista-agent diagnose`."
    )
    return 0


def _policy(settings: AgentSettings, args: argparse.Namespace) -> int:
    policy.require_elevation(settings, f"O comando {args.command}")
    if args.command == "allow":
        allowed = policy.allow(settings, args.package)
        print(f"Robô {args.package} liberado. Permitidos: {', '.join(allowed)}.")
    elif args.command == "disallow":
        allowed = policy.disallow(settings, args.package)
        print(f"Robô {args.package} removido. Permitidos: {', '.join(allowed) or 'nenhum'}.")
    elif args.command == "pause":
        policy.pause(settings)
        print("Kill switch ligado: o agente não pega novas execuções (a atual termina).")
    else:
        policy.resume(settings)
        print("Kill switch desligado.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    # The console may not speak UTF-8 (Windows): never die on an accent.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    try:
        # The host and the body of a service never read `agent.toml`: the host has no right to.
        if args.command == "host":
            raise SystemExit(_host(args))
        if args.command == "service" and args.service_command == "run":
            raise SystemExit(_service_run(args))
        settings = AgentSettings()
        if args.command == "enroll":
            code = _enroll(settings, args)
        elif args.command == "run":
            code = _run(settings, args)
        elif args.command == "setup":
            code = _setup(settings, args)
        elif args.command in ("allow", "disallow", "pause", "resume"):
            code = _policy(settings, args)
        elif args.command == "service":
            code = _service(settings, args)
        else:
            code = _diagnose(settings)
    except MachineRevoked as exc:
        logging.getLogger("regista_agent").error("%s", exc)
        print(exc, file=sys.stderr)
        code = exc.exit_code
    except AgentError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        code = exc.exit_code
    except KeyboardInterrupt:
        code = EXIT_ERROR
    raise SystemExit(code)
