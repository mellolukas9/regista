"""Shared setup of the run tests: a pool and a bot, machines, and direct state changes."""

import uuid
from typing import Any

import httpx
from sqlalchemy import text

from regista_api.core.db import tenant_session

from .agent_helpers import AgentSim
from .helpers import Panel, csrf


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _post(
    client: httpx.AsyncClient, path: str, body: dict[str, object] | None = None
) -> httpx.Response:
    return await client.post(path, json=body, headers=csrf(client))


async def make_bot(panel: Panel, tenant_id: uuid.UUID, owner: httpx.AsyncClient) -> dict[str, Any]:
    """A pool (by the client's admin) and a bot in it (by the staff, the only one who can)."""
    pool = await _post(owner, "/pools", {"name": f"pool-{_suffix()}"})
    assert pool.status_code == 201, pool.text
    staff = await panel.staff_in(tenant_id)
    s = _suffix()
    bot = await _post(
        staff,
        "/bots",
        {"name": f"Bot {s}", "package_name": f"pacote_{s}", "pool_id": pool.json()["id"]},
    )
    assert bot.status_code == 201, bot.text
    body: dict[str, Any] = bot.json()
    return body


async def make_machine(panel: Panel, tenant_id: uuid.UUID, pool_id: str) -> uuid.UUID:
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=tenant_id) as db:
        machine_id: uuid.UUID = (
            await db.execute(
                text(
                    "INSERT INTO machines (tenant_id, pool_id, name) VALUES (:t, :p, :n)"
                    " RETURNING id"
                ),
                {"t": tenant_id, "p": pool_id, "n": f"m-{_suffix()}"},
            )
        ).scalar_one()
    return machine_id


async def set_job(panel: Panel, tenant_id: uuid.UUID, job_id: str, **fields: object) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with tenant_session(panel.env.app.state.session_factory, tenant_id=tenant_id) as db:
        await db.execute(
            text(f"UPDATE jobs SET {sets} WHERE id = :id"),  # noqa: S608  (test-only columns)
            {"id": job_id, **fields},
        )


async def run(client: httpx.AsyncClient, bot_id: str) -> dict[str, Any]:
    r = await _post(client, "/jobs", {"bot_id": bot_id, "params": {}})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def enrolled_agent(
    panel: Panel,
    tenant_id: uuid.UUID,
    owner: httpx.AsyncClient,
    pool_id: str,
    agent_client: httpx.AsyncClient,
) -> AgentSim:
    """A machine of the client's pool, registered in the panel and enrolled by a simulated agent
    that then holds a valid access token. The machine is `online`."""
    created = await _post(
        owner,
        "/machines",
        {"name": f"m-{_suffix()}", "pool_id": pool_id, "mode": "service"},
    )
    assert created.status_code == 201, created.text
    from regista_api.machines.agent import audience

    agent = AgentSim(agent_client, audience(panel.env.app.state.settings.api_public_url))
    enrolled = await agent.enroll(created.json()["enrollment_key"])
    assert enrolled.status_code == 200, enrolled.text
    await agent.login()
    return agent
