import uuid
from datetime import datetime
from pydantic import BaseModel


class RunCreate(BaseModel):
    bot_id: uuid.UUID
    triggered_by: str = "manual"


class RunRead(BaseModel):
    id: uuid.UUID
    bot_id: uuid.UUID
    prefect_flow_run_id: str | None
    status: str
    triggered_by: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
