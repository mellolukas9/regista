"""Request and response models for signed versions of a bot (design-system.md 7.6, ADR 0021)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class CurrentVersion(BaseModel):
    id: uuid.UUID
    version: str


class VersionItem(BaseModel):
    id: uuid.UUID
    version: str
    package_sha256: str
    size_bytes: int
    release_note: str | None
    published_at: datetime
    # "Por" is always the Artemisys team: only they publish, and no person is named to a client.
    published_by: str
    is_current: bool
    python: str
    playwright: str | None
    chromium_revision: str | None


class VersionList(BaseModel):
    items: list[VersionItem]
    current_version_id: uuid.UUID | None


class StartUploadRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"signature": "{}", "release_note": "Primeira versão do robô."}]
        },
    )
    # The content of the `.rgsig` file, exactly as `regista-pack` wrote it.
    signature: Annotated[str, Field(min_length=2, max_length=65_536)]
    release_note: Annotated[str | None, Field(max_length=2000)] = None


class UploadTicket(BaseModel):
    version_id: uuid.UUID
    version: str
    # A PUT that works for this object, this type and exactly this size, for `expires_in` seconds.
    upload_url: str
    upload_headers: dict[str, str]
    expires_in: int


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"activate": False}]})
    # "Colocar em uso assim que for publicada". The activation has its own audit entry.
    activate: bool = False


class SetCurrentVersionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"version_id": "00000000-0000-4000-8000-000000000000"}]},
    )
    version_id: uuid.UUID
