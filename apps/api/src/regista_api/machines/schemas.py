"""Request and response models for pools and machines.

Every request model carries `examples`; the isolation sweep in the tests builds bodies from them.
Anything that came from the agent (`os_info`, `agent_version`, event metadata) is untrusted data:
it is size-limited on the way in and the panel shows it escaped.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from regista_api.tenants.schemas import Page

MACHINE_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"

MachineName = Annotated[str, StringConstraints(pattern=MACHINE_NAME_PATTERN)]
MachineStatus = Literal["pending", "online", "offline", "revoked"]
MachineMode = Literal["service", "session", "oneshot"]
EventKind = Literal[
    "enrolled",
    "re_enrolled",
    "first_signal",
    "went_offline",
    "came_back",
    "agent_updated",
    "revoked",
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- pools ------------------------------------------------------------------------------------


class CreatePoolRequest(_Request):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"name": "Escritório - Atendimento"}]}
    )
    name: Annotated[str, Field(min_length=1, max_length=80)]


class PoolItem(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    client_id: uuid.UUID
    client_name: str
    machines_total: int
    machines_online: int
    created_at: datetime


class PoolList(BaseModel):
    items: list[PoolItem]


# --- machines ---------------------------------------------------------------------------------


class CreateMachineRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "estacao-atendimento-03",
                    "pool_id": "00000000-0000-4000-8000-000000000000",
                    "mode": "service",
                }
            ]
        },
    )
    name: MachineName
    pool_id: uuid.UUID
    # `oneshot` exists in the schema but is out of the MVP: the API only takes the first two.
    mode: Literal["service", "session"]


class RevokeMachineRequest(_Request):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"confirm_name": "estacao-atendimento-03"}]}
    )
    confirm_name: Annotated[str, Field(min_length=1, max_length=64)]


class MachineItem(BaseModel):
    id: uuid.UUID
    name: str
    status: MachineStatus
    mode: MachineMode
    pool_id: uuid.UUID
    pool_name: str
    client_id: uuid.UUID
    client_name: str
    last_seen_at: datetime | None
    agent_version: str | None
    # The live (unused, not revoked) enrollment key, if any. The key itself is never returned
    # after creation; the panel uses this to say "Chave expirada, gere uma nova".
    key_expires_at: datetime | None
    key_created_at: datetime | None
    created_at: datetime


class MachineList(Page):
    items: list[MachineItem]
    revoked_count: int


class MachineDetail(MachineItem):
    os_info: dict[str, str]
    max_concurrency: int
    enrolled_at: datetime | None
    created_by: str | None
    revoked_at: datetime | None


class IssuedKey(BaseModel):
    """The only response that ever carries an enrollment key (`Cache-Control: no-store`)."""

    machine_id: uuid.UUID
    name: str
    enrollment_key: str
    expires_at: datetime
    # The address the agent must use in `enroll --url`: the audience the server verifies against.
    server_url: str


class MachineEvent(BaseModel):
    id: uuid.UUID
    kind: EventKind
    created_at: datetime
    metadata: dict[str, Any]


class MachineEventList(Page):
    items: list[MachineEvent]


class ClientNoSignal(BaseModel):
    client_id: uuid.UUID
    client_name: str
    no_signal: int


class MachinesSummary(BaseModel):
    total: int
    online: int
    no_signal: int
    # Only in "all clients": the clients that have at least one machine without signal.
    clients: list[ClientNoSignal]
