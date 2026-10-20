"""Users, organisations, memberships and refresh tokens."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aqua_core.db import Base
from aqua_core.models._types import Timestamps, UUIDPk, str_enum
from aqua_core.models.enums import OrgRole, OrgType, PlatformRole


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), unique=True)  # stored lower-case
    full_name: Mapped[str] = mapped_column(sa.String(200))
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    platform_role: Mapped[PlatformRole] = mapped_column(
        str_enum(PlatformRole, "platform_role"), default=PlatformRole.USER
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    # TOTP two-factor auth (RFC 6238). The secret is Fernet-encrypted (MFA_ENCRYPTION_KEY); it is
    # set by /auth/mfa/setup and becomes active once /auth/mfa/enable confirms a code.
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Highest TOTP time step accepted so far: a code is only valid once (replay protection).
    mfa_last_used_step: Mapped[int | None] = mapped_column(sa.BigInteger)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def mfa_enabled(self) -> bool:
        return self.mfa_enabled_at is not None


class MfaRecoveryCode(UUIDPk, Base):
    """One-time MFA recovery codes. Only a SHA-256 of each (80-bit random) code is stored."""

    __tablename__ = "mfa_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Organization(UUIDPk, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(sa.String(200))
    slug: Mapped[str] = mapped_column(sa.String(120), unique=True)
    org_type: Mapped[OrgType] = mapped_column(str_enum(OrgType, "org_type"))

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Membership(Timestamps, Base):
    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[OrgRole] = mapped_column(str_enum(OrgRole, "org_role"))

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships", lazy="selectin")


class RefreshToken(UUIDPk, Base):
    """Rotating refresh tokens. Only a SHA-256 of the token is stored. Reusing a rotated token
    revokes the whole family (theft detection)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
