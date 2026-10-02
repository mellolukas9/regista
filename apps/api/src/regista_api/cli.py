"""`regista-admin`: operations for the Artemisys team (see admin.py).

    uv run regista-admin create-platform-admin --email pessoa@artemisys.com.br --name "Pessoa"
    uv run regista-admin resend-platform-admin-invite --email pessoa@artemisys.com.br
    uv run regista-admin remove-platform-admin --email pessoa@artemisys.com.br
    uv run regista-admin seed-dev

There is deliberately no password option: people get an invitation and choose their own.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from regista_api import admin
from regista_api.auth.deps import AppState
from regista_api.core.config import ConfigurationError, get_settings
from regista_api.core.db import create_engine, create_session_factory
from regista_api.core.email import create_email_sender
from regista_api.core.keys import LocalKeyProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regista-admin", description="Operações da equipe Artemisys no Regista."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-platform-admin", help="cria um administrador da plataforma")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help="nome exibido na barra lateral")

    resend = sub.add_parser(
        "resend-platform-admin-invite",
        help="reenvia o convite (invalida senha, MFA e sessões atuais)",
    )
    resend.add_argument("--email", required=True)

    remove = sub.add_parser("remove-platform-admin", help="remove o acesso de um administrador")
    remove.add_argument("--email", required=True)

    sub.add_parser("seed-dev", help="cria os dados de exemplo do ambiente de desenvolvimento")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.validate_for_runtime()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    state = AppState(
        settings=settings,
        factory=factory,
        keys=LocalKeyProvider(settings.master_key),
        email=create_email_sender(settings),
    )
    try:
        if args.command == "create-platform-admin":
            await admin.create_platform_admin(factory, state, email=args.email, name=args.name)
            print(f"Convite enviado para {args.email}.")
        elif args.command == "resend-platform-admin-invite":
            await admin.resend_platform_admin_invite(factory, state, email=args.email)
            print(f"Convite reenviado para {args.email}. Senha, MFA e sessões anteriores caíram.")
        elif args.command == "remove-platform-admin":
            await admin.remove_platform_admin(factory, email=args.email)
            print(f"Acesso de {args.email} removido.")
        else:
            _print_seed(await admin.seed_dev(factory, state))
    finally:
        await engine.dispose()
    return 0


def _print_seed(result: admin.SeedResult) -> None:
    print("\nSeed de desenvolvimento criado. Estas credenciais aparecem só desta vez:\n")
    for user in result.users:
        print(f"{user.email}  ({user.role}, {user.client})")
        if user.invitation_sent:
            print("  convite enviado: o link está no e-mail impresso acima")
            continue
        print(f"  senha:       {user.password}")
        if user.totp_secret:
            print(f"  chave TOTP:  {user.totp_secret}")
            print(f"  otpauth:     {user.otpauth_uri}")
        else:
            print("  sem MFA: o primeiro acesso pede para configurar")
        if user.email.startswith("admin@"):
            print(f"  códigos de recuperação: {' '.join(user.recovery_codes)}")
    print()


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        code = asyncio.run(_run(args))
    except (admin.AdminError, ConfigurationError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
