"""Request and response models for runs ("execuções"; `jobs` in the code), panel side."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from regista_api.tenants.schemas import Page

JobStatus = Literal["pending", "assigned", "running", "completed", "failed", "cancelled"]
Period = Literal["today", "7d", "30d", "all"]
ACTIVE_STATUSES = ("pending", "assigned", "running")
FINAL_STATUSES = ("completed", "failed", "cancelled")


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"bot_id": "00000000-0000-4000-8000-000000000000", "params": {}}]
        },
    )
    bot_id: uuid.UUID
    # The panel sends `{}` in M3 (there is no form for it); the robot has its own defaults.
    params: Annotated[dict[str, Any], Field(default_factory=dict)]


class JobItem(BaseModel):
    id: uuid.UUID
    short_code: str
    bot_id: uuid.UUID
    bot_name: str
    status: JobStatus
    trigger: str
    # "Manual" shows the person's e-mail; Artemisys staff show as "Equipe Artemisys".
    triggered_by: str | None
    machine_id: uuid.UUID | None
    machine_name: str | None
    pool_id: uuid.UUID
    pool_name: str
    client_id: uuid.UUID
    client_name: str
    created_at: datetime
    assigned_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    # Why a package was refused (closed list, only for package_invalid): fixed text in the panel.
    error_reason: str | None
    # The version the run was taken with ("v1.2.0" in the run detail); null in development.
    bot_version: str | None
    items_successful: int
    items_failed: int
    items_abandoned: int
    items_total: int


class JobDetail(JobItem):
    package_name: str
    params: dict[str, Any]
    # True while the pool has no online machine: the Timeline shows "nenhuma máquina livre".
    pool_has_online_machine: bool


class JobList(Page):
    items: list[JobItem]


class JobsSummary(BaseModel):
    pending: int
    active: int
