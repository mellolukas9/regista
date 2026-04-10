"""
Background task que sincroniza o status das runs ativas com o Prefect.
Roda a cada POLL_INTERVAL segundos enquanto o FastAPI estiver no ar.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.run import Run, RunStatus
from app.services import prefect as prefect_service

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30  # segundos

# Mapeamento de estados do Prefect → RunStatus
_PREFECT_STATE_MAP: dict[str, RunStatus] = {
    "SCHEDULED": RunStatus.running,
    "PENDING":   RunStatus.running,
    "RUNNING":   RunStatus.running,
    "PAUSED":    RunStatus.running,
    "COMPLETED": RunStatus.completed,
    "FAILED":    RunStatus.failed,
    "CRASHED":   RunStatus.failed,
    "CANCELLED":  RunStatus.cancelled,
    "CANCELLING": RunStatus.cancelled,
}


async def _sync_once() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Run).where(
                Run.status.in_([RunStatus.pending, RunStatus.running]),
                Run.prefect_flow_run_id.isnot(None),
            )
        )
        runs = result.scalars().all()

        if not runs:
            return

        for run in runs:
            try:
                flow_run = await prefect_service.get_flow_run(run.prefect_flow_run_id)
                state_type = flow_run.get("state", {}).get("type", "").upper()
                new_status = _PREFECT_STATE_MAP.get(state_type)

                if new_status is None or new_status == run.status:
                    continue

                run.status = new_status

                if new_status == RunStatus.running and run.started_at is None:
                    run.started_at = datetime.now(timezone.utc)

                if new_status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):
                    run.finished_at = datetime.now(timezone.utc)

            except Exception:
                logger.debug("Não foi possível sincronizar run %s", run.id)

        await db.commit()


async def run_sync_loop() -> None:
    logger.info("Run sync iniciado (intervalo: %ds)", POLL_INTERVAL)
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            await _sync_once()
        except Exception:
            logger.exception("Erro no ciclo de sync de runs")
