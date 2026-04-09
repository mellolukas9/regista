"""
Integração com o Prefect OSS via REST API.
Usa httpx async para não bloquear o event loop do FastAPI.
"""
import uuid
import httpx

from app.core.config import settings


async def trigger_flow_run(deployment_id: str, parameters: dict | None = None) -> dict:
    """Dispara um flow run a partir de um deployment_id do Prefect."""
    url = f"{settings.PREFECT_API_URL}/deployments/{deployment_id}/create_flow_run"
    payload = {"parameters": parameters or {}}

    async with httpx.AsyncClient(timeout=30) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def get_flow_run(flow_run_id: str) -> dict:
    """Retorna o estado atual de um flow run."""
    url = f"{settings.PREFECT_API_URL}/flow_runs/{flow_run_id}"

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def get_flow_run_logs(flow_run_id: str, limit: int = 200) -> list[dict]:
    """Retorna os logs de um flow run."""
    url = f"{settings.PREFECT_API_URL}/logs/filter"
    payload = {
        "logs": {"flow_run_id": {"any_": [flow_run_id]}},
        "limit": limit,
        "sort": "TIMESTAMP_ASC",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def cancel_flow_run(flow_run_id: str) -> None:
    """Cancela um flow run em execução."""
    url = f"{settings.PREFECT_API_URL}/flow_runs/{flow_run_id}/set_state"
    payload = {"state": {"type": "CANCELLED"}, "force": True}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
