"""Request and response models for bots (docs/specs/design-system.md 7.5 and 7.6)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from regista_api.bots.version_schemas import CurrentVersion
from regista_api.tenants.schemas import Page

# The robot's folder in development and its package from M4 on. Never changes after creation.
PACKAGE_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
PackageName = Annotated[str, StringConstraints(pattern=PACKAGE_NAME_PATTERN)]


class CreateBotRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "Busca na Wikipédia",
                    "package_name": "demo_busca_wikipedia",
                    "pool_id": "00000000-0000-4000-8000-000000000000",
                    "description": "Pesquisa um termo na Wikipédia e tira uma captura de tela.",
                }
            ]
        },
    )
    name: Annotated[str, Field(min_length=1, max_length=120)]
    package_name: PackageName
    pool_id: uuid.UUID
    description: Annotated[str | None, Field(max_length=500)] = None


class LastRun(BaseModel):
    id: uuid.UUID
    short_code: str
    status: str
    created_at: datetime
    finished_at: datetime | None


class BotItem(BaseModel):
    id: uuid.UUID
    name: str
    package_name: str
    description: str | None
    pool_id: uuid.UUID
    pool_name: str
    client_id: uuid.UUID
    client_name: str
    is_active: bool
    created_at: datetime
    last_run: LastRun | None
    # Status of the last 10 runs, oldest first (the "Últimas 10" strip).
    recent_statuses: list[str]
    # The ids of those runs, in the same order (the strip links to each of them).
    recent_ids: list[uuid.UUID]
    # True while a run of this bot is pending, assigned or running ("Executar agora" tooltip).
    has_active_run: bool
    # The version in use (null until one is put in use): "v1.2.0 em uso".
    current_version: CurrentVersion | None


class BotList(Page):
    items: list[BotItem]


class BotDetail(BotItem):
    machines_total: int
    machines_online: int
    # Finished runs of the last 30 days; the rate is null when there are none.
    runs_30d: int
    success_rate_30d: float | None
