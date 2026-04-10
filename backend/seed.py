"""
Script de seed para criação do primeiro admin e cliente padrão.

Uso:
    python seed.py
    python seed.py --email admin@regista.io --password minhasenha --client "Minha Empresa"
"""
import asyncio
import argparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import hash_password
from app.models import *  # noqa: F401, F403 — registra todos os models no metadata
from app.models.client import Client
from app.models.user import User, UserRole


async def seed(email: str, password: str, client_name: str, client_slug: str) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # Verifica se o usuário já existe
        result = await db.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            print(f"[!] Usuário '{email}' já existe. Nada a fazer.")
            await engine.dispose()
            return

        # Cria cliente se não existir
        result = await db.execute(select(Client).where(Client.slug == client_slug))
        client = result.scalar_one_or_none()

        if not client:
            client = Client(name=client_name, slug=client_slug, primary_color="#2563eb")
            db.add(client)
            await db.flush()
            print(f"[+] Cliente criado: {client.name} (id={client.id})")
        else:
            print(f"[~] Cliente já existe: {client.name} (id={client.id})")

        # Cria usuário admin
        user = User(
            client_id=client.id,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.admin,
        )
        db.add(user)
        await db.commit()
        print(f"[+] Admin criado: {email} (id={user.id})")

    await engine.dispose()
    print("\nSeed concluído. Acesse o portal com as credenciais acima.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed inicial do Regista")
    parser.add_argument("--email",   default="admin@regista.io",  help="E-mail do admin")
    parser.add_argument("--password", default="admin123",          help="Senha do admin")
    parser.add_argument("--client",  default="Regista",            help="Nome do cliente padrão")
    parser.add_argument("--slug",    default="regista",            help="Slug do cliente padrão")
    args = parser.parse_args()

    asyncio.run(seed(args.email, args.password, args.client, args.slug))


if __name__ == "__main__":
    main()
