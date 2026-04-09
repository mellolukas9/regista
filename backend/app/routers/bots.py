import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.bot import Bot
from app.models.user import User
from app.schemas.bot import BotCreate, BotRead
from app.services.auth import get_current_user

router = APIRouter(prefix="/bots", tags=["bots"])


def _bot_query(client_id: uuid.UUID, user: User):
    """Admins veem todos os bots; viewers só veem os do próprio client."""
    q = select(Bot)
    if user.role != "admin":
        q = q.where(Bot.client_id == client_id)
    return q


@router.get("/", response_model=list[BotRead])
async def list_bots(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = _bot_query(current_user.client_id, current_user).order_by(Bot.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{bot_id}", response_model=BotRead)
async def get_bot(
    bot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Bot).where(Bot.id == bot_id))
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot não encontrado")
    if current_user.role != "admin" and bot.client_id != current_user.client_id:
        raise HTTPException(status_code=403, detail="Acesso negado")
    return bot


@router.post("/", response_model=BotRead, status_code=201)
async def create_bot(
    body: BotCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client_id = current_user.client_id if current_user.role != "admin" else current_user.client_id
    bot = Bot(**body.model_dump(), client_id=client_id)
    db.add(bot)
    await db.flush()
    await db.refresh(bot)
    return bot
