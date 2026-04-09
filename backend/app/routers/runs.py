import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.bot import Bot
from app.models.run import Run, RunStatus, RunTrigger
from app.models.user import User
from app.schemas.run import RunCreate, RunRead
from app.services.auth import get_current_user
from app.services import prefect as prefect_service

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("/", response_model=list[RunRead])
async def list_runs(
    bot_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Run).join(Bot, Run.bot_id == Bot.id)
    if current_user.role != "admin":
        q = q.where(Bot.client_id == current_user.client_id)
    if bot_id:
        q = q.where(Run.bot_id == bot_id)
    q = q.order_by(Run.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/", response_model=RunRead, status_code=201)
async def trigger_run(
    body: RunCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verifica se o bot pertence ao cliente
    result = await db.execute(select(Bot).where(Bot.id == body.bot_id))
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot não encontrado")
    if current_user.role != "admin" and bot.client_id != current_user.client_id:
        raise HTTPException(status_code=403, detail="Acesso negado")
    if not bot.is_active:
        raise HTTPException(status_code=400, detail="Bot inativo")

    run = Run(
        bot_id=bot.id,
        triggered_by=RunTrigger(body.triggered_by),
        status=RunStatus.pending,
    )
    db.add(run)
    await db.flush()

    # Dispara no Prefect se houver deployment configurado
    if bot.prefect_deployment_id:
        try:
            prefect_run = await prefect_service.trigger_flow_run(bot.prefect_deployment_id)
            run.prefect_flow_run_id = prefect_run.get("id")
            run.status = RunStatus.running
        except Exception as exc:
            run.status = RunStatus.failed

    await db.refresh(run)
    return run


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run não encontrada")
    return run


@router.get("/{run_id}/logs")
async def get_run_logs(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run não encontrada")
    if not run.prefect_flow_run_id:
        return {"logs": []}
    logs = await prefect_service.get_flow_run_logs(run.prefect_flow_run_id)
    return {"logs": logs}
