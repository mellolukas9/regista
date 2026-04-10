import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.machine import Machine, MachineStatus
from app.models.user import User
from app.schemas.machine import MachineCreate, MachineRead
from app.services.auth import get_current_user, require_admin

router = APIRouter(prefix="/machines", tags=["machines"])


@router.get("/", response_model=list[MachineRead])
async def list_machines(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Machine)
    if current_user.role != "admin":
        q = q.where(Machine.client_id == current_user.client_id)
    result = await db.execute(q.order_by(Machine.created_at.desc()))
    return result.scalars().all()


@router.post("/", response_model=MachineRead, status_code=201)
async def create_machine(
    body: MachineCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    machine = Machine(**body.model_dump(), client_id=current_user.client_id)
    db.add(machine)
    await db.flush()
    await db.refresh(machine)
    return machine


@router.delete("/{machine_id}", status_code=204)
async def delete_machine(
    machine_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    result = await db.execute(select(Machine).where(Machine.id == machine_id))
    machine = result.scalar_one_or_none()
    if not machine:
        raise HTTPException(status_code=404, detail="Máquina não encontrada")
    await db.delete(machine)


@router.post("/{machine_id}/heartbeat", response_model=MachineRead)
async def heartbeat(
    machine_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Chamado pelo worker para marcar a máquina como online."""
    result = await db.execute(select(Machine).where(Machine.id == machine_id))
    machine = result.scalar_one_or_none()
    if not machine:
        raise HTTPException(status_code=404, detail="Máquina não encontrada")
    if current_user.role != "admin" and machine.client_id != current_user.client_id:
        raise HTTPException(status_code=403, detail="Acesso negado")

    machine.status = MachineStatus.online
    machine.last_seen_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(machine)
    return machine
