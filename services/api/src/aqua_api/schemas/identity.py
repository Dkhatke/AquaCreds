import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from aqua_core.models import OrgRole, OrgType, PlatformRole


class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth2 token type, not a secret)
    expires_in: int


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    org_id: uuid.UUID
    role: OrgRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    platform_role: PlatformRole
    created_at: datetime
    memberships: list[MembershipOut] = []


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    org_type: OrgType


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    org_type: OrgType
    created_at: datetime


class MemberAdd(BaseModel):
    email: EmailStr
    role: OrgRole

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: OrgRole
