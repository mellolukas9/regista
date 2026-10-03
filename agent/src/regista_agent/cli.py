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
from regista_agent import logs, loop, sysinfo
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
