import uuid
from datetime import datetime
from typing import Literal

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
    recovery_codes_remaining: int | None = Field(
        default=None,
        description="Set only when a recovery code was used and few remain: regenerate them",
    )


class MfaChallenge(BaseModel):
    """Returned by /auth/login instead of tokens when the account has TOTP enabled."""

    mfa_required: Literal[True] = True
    mfa_token: str
    expires_in: int


class MfaVerifyIn(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=2000)
    code: str = Field(min_length=6, max_length=32, description="6-digit TOTP or a recovery code")


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaSetupOut(BaseModel):
    secret: str = Field(description="Base32 secret for manual entry in an authenticator app")
    otpauth_uri: str
    qr_svg: str = Field(description="Inline SVG QR code of otpauth_uri")


class MfaEnabledOut(BaseModel):
    recovery_codes: list[str] = Field(description="Shown once; each code works once")


class MfaStatusOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    recovery_codes_remaining: int = Field(description="Unused recovery codes")
    required: bool = Field(
        description="True when this account must have MFA (staff while MFA_REQUIRED_FOR_STAFF)"
    )


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
    mfa_enabled: bool = False
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
