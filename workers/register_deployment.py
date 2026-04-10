"""
Registra um script de bot como deployment no Prefect e salva o deployment_id
para uso no Regista.

Uso:
    python register_deployment.py bot_relatorio_vendas.py:meu_bot \
        --name "Relatório de Vendas" \
        --queue queue-rdp-01

O script imprime o deployment_id ao final — use-o ao cadastrar o bot no Regista.
"""
import argparse
import asyncio
import sys

from prefect.deployments import Deployment
from prefect.infrastructure import Process


async def register(entrypoint: str, name: str, queue: str, description: str) -> None:
    # entrypoint formato: "arquivo.py:nome_do_flow"
    parts = entrypoint.split(":")
    if len(parts) != 2:
        print("Erro: entrypoint deve ter formato arquivo.py:nome_do_flow")
        sys.exit(1)

    script_path, flow_name = parts

    # Importa o flow dinamicamente
    import importlib.util
    spec = importlib.util.spec_from_file_location("bot_module", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flow_obj = getattr(module, flow_name)

    deployment = await Deployment.build_from_flow(
        flow=flow_obj,
        name=name,
        work_queue_name=queue,
        work_pool_name=queue,
        description=description or "",
        apply=True,
    )

    print(f"\nDeployment registrado com sucesso!")
    print(f"  Nome:          {name}")
    print(f"  Fila:          {queue}")
    print(f"  Deployment ID: {deployment.id}")
    print(f"\nUse este ID ao cadastrar o bot no Regista (campo 'prefect_deployment_id').")


def main() -> None:
    parser = argparse.ArgumentParser(description="Registra um bot como deployment no Prefect")
    parser.add_argument("entrypoint", help="arquivo.py:nome_do_flow")
    parser.add_argument("--name",        required=True, help="Nome do deployment no Prefect")
    parser.add_argument("--queue",       default="queue-rdp-01", help="Work queue/pool")
    parser.add_argument("--description", default="",             help="Descrição opcional")
    args = parser.parse_args()

    asyncio.run(register(args.entrypoint, args.name, args.queue, args.description))


if __name__ == "__main__":
    main()
