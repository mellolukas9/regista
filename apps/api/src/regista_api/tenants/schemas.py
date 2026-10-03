"""Request and response models for clients, users and the account.

Every request model carries `examples`; the isolation sweep in the tests builds bodies from them.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from regista_api.auth.schemas import Email, Password

TenantRole = Literal["tenant_admin", "operator", "viewer"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel):
    total: int
    page: int
    per_page: int


# --- clients ----------------------------------------------------------------------------------


class ClientItem(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    users_count: int
    machines_total: int
    machines_online: int


class ClientList(Page):
    items: list[ClientItem]


class CreateClientRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "Escritório Exemplo", "admin_email": "admin@exemplo.com.br"}]
        },
    )
    name: Annotated[str, Field(min_length=2, max_length=120)]
    admin_email: Email


class CreateClientResponse(BaseModel):
    id: uuid.UUID
    name: str
    admin_email: str


# --- users ------------------------------------------------------------------------------------


class UserItem(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    # "active": MFA on; "none": password set but MFA not finished; null: invitation not accepted.
    mfa: Literal["active", "none"] | None
    last_login_at: datetime | None
    client_id: uuid.UUID
    client_name: str
    is_self: bool


class UserList(Page):
    items: list[UserItem]


class InviteUserRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"email": "pessoa@exemplo.com.br", "role": "operator"}]},
    )
    email: Email
    role: TenantRole


class ChangeRoleRequest(_Request):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"role": "viewer"}]})
    role: TenantRole


class UserSummary(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str


class RevokedResponse(BaseModel):
    revoked: int


# --- account ----------------------------------------------------------------------------------


class SessionItem(BaseModel):
    id: uuid.UUID
    device: str
    ip: str | None
    created_at: datetime
    last_seen_at: datetime
    is_current: bool


class SessionList(BaseModel):
    items: list[SessionItem]


class RegenerateCodesRequest(_Request):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"password": "uma senha qualquer"}]}
    )
    password: Password


class RegeneratedCodesResponse(BaseModel):
    recovery_codes: list[str]


class ChangePasswordRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "current_password": "a senha atual de teste",
                    "new_password": "uma senha nova bem longa",
                    "code": "123456",
                }
            ]
        },
    )
    current_password: Password
    new_password: Password
    code: Annotated[str, Field(min_length=1, max_length=32)]
