import uuid
from datetime import datetime
from pydantic import BaseModel


class ClientCreate(BaseModel):
    name: str
    slug: str
    logo_url: str | None = None
    primary_color: str | None = None


class ClientRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    logo_url: str | None
    primary_color: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
