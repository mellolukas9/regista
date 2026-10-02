"""Request and response models of the auth routes.

Every request model carries `examples`: the cross-tenant isolation sweep builds its bodies
from them (a model without examples fails that test on purpose).
"""

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254
PASSWORD_MAX = 256  # bounds argon2 work per request


def _normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL.match(value):
        raise ValueError("invalid e-mail")
    return value


Email = Annotated[str, Field(max_length=EMAIL_MAX), AfterValidator(_normalize_email)]
Password = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX)]
Token = Annotated[str, Field(min_length=1, max_length=256)]
Code = Annotated[str, Field(min_length=1, max_length=32)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"email": "pessoa@example.com", "password": "uma senha qualquer"}]
        },
    )
    email: Email
    password: Password


class InvitationTokenRequest(_Request):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"token": "token-do-convite"}]}
    )
    token: Token


class AcceptInvitationRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"token": "token-do-convite", "password": "uma senha longa e unica"}]
        },
    )
    token: Token
    password: Password


class CodeRequest(_Request):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"code": "123456"}]})
    code: Code


class RecoveryCodeRequest(_Request):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"recovery_code": "ABCD-2345"}]}
    )
    recovery_code: Code


class StageResponse(BaseModel):
    stage: str


class InvitationInspectResponse(BaseModel):
    email: str


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesResponse(BaseModel):
    stage: str
    recovery_codes: list[str]


class MeResponse(BaseModel):
    stage: str
    email: str
    role: str | None = None
    display_name: str | None = None
    is_platform_admin: bool = False
    tenant_id: str | None = None
    tenant_name: str | None = None
    mfa_enabled: bool = False
    mfa_enabled_at: datetime | None = None
    recovery_codes_remaining: int | None = None
