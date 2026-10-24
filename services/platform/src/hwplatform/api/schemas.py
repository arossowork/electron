from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    tenant_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    tenant_id: uuid.UUID | None = None  # pick one of the user's tenants; default: the oldest membership


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=512)


class AcceptedOut(BaseModel):
    detail: str


class VerifyIn(BaseModel):
    token: str = Field(max_length=512)


class AcceptInviteIn(BaseModel):
    token: str = Field(max_length=512)


class AcceptInviteOut(BaseModel):
    tenant_id: uuid.UUID
    role: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    tenant_id: uuid.UUID
    user_id: uuid.UUID


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    max_concurrent_runs: int
    created_at: datetime


class TenantRef(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    tenant_id: uuid.UUID
    role: str
    tenants: list[TenantRef]


class MemberIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "member"] = "member"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,59}$")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    created_by: uuid.UUID | None
    created_at: datetime
