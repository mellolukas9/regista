import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.bot import Bot
from app.models.schedule import Schedule
from app.models.user import User
from app.schemas.schedule import ScheduleCreate, ScheduleRead
from app.services.auth import get_current_user

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("/", response_model=list[ScheduleRead])
async def list_schedules(
    bot_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Schedule).join(Bot, Schedule.bot_id == Bot.id)
    if current_user.role != "admin":
        q = q.where(Bot.client_id == current_user.client_id)
    if bot_id:
        q = q.where(Schedule.bot_id == bot_id)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/", response_model=ScheduleRead, status_code=201)
async def create_schedule(
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Bot).where(Bot.id == body.bot_id))
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot não encontrado")
    if current_user.role != "admin" and bot.client_id != current_user.client_id:
        raise HTTPException(status_code=403, detail="Acesso negado")

    schedule = Schedule(**body.model_dump())
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    await db.delete(schedule)
