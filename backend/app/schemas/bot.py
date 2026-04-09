import uuid
from datetime import datetime
from pydantic import BaseModel


class BotCreate(BaseModel):
    name: str
    description: str | None = None
    prefect_deployment_id: str | None = None
    queue_name: str | None = None
    machine_id: uuid.UUID | None = None
    is_active: bool = True


class BotRead(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    description: str | None
    prefect_deployment_id: str | None
    queue_name: str | None
    machine_id: uuid.UUID | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
