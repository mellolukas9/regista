import uuid
from datetime import datetime
from pydantic import BaseModel

from app.models.machine import MachineType, MachineStatus


class MachineCreate(BaseModel):
    name: str
    type: MachineType = MachineType.rdp
    queue_name: str


class MachineRead(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    type: MachineType
    queue_name: str
    status: MachineStatus
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
