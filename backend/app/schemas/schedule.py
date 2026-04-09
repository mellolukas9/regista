import uuid
from datetime import datetime
from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    bot_id: uuid.UUID
    cron_expression: str
    is_active: bool = True


class ScheduleRead(BaseModel):
    id: uuid.UUID
    bot_id: uuid.UUID
    cron_expression: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
