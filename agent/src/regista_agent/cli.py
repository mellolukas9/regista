"""`regista-agent`: enroll, run and diagnose (docs/specs/agent.md).

Exit codes: 0 ok, 1 something went wrong, 2 wrong usage, 3 the machine was revoked.
"""

import argparse
import logging
import sys
import threading
from collections.abc import Sequence

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
        help="Conta que roda o agente e que poderá ler a chave. No Windows, o padrão é "
        "NT SERVICE\\RegistaAgent (modo Serviço); no modo Sessão, informe o usuário dedicado.",
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
        settings, url=args.url, key=args.key, agent_account=args.agent_account, force=args.force
    )
    print(f"Máquina cadastrada: {result.machine_id} (modo {result.mode}).")
    print(f"Configuração gravada em {result.config_path}.")
    if result.agent_account:
        print(f"A chave só pode ser lida por {result.agent_account}, SYSTEM e Administradores.")
    print("Agora rode `regista-agent run`, ou instale o agente como serviço.")
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
    if sys.platform == "win32" and settings.agent_account:
        from regista_agent import _windows

        agent_sid = _windows.resolve_sid(settings.agent_account)
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
    chosen = runtime.run_setup(settings, needs, agent_sid=agent_sid)
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
        settings = AgentSettings()
        if args.command == "enroll":
            code = _enroll(settings, args)
        elif args.command == "run":
            code = _run(settings, args)
        elif args.command == "setup":
            code = _setup(settings, args)
        elif args.command in ("allow", "disallow", "pause", "resume"):
            code = _policy(settings, args)
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
