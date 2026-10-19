"""Typed runtime configuration, read from environment variables (and `.env` in development)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = "postgresql+asyncpg://aquacreds:aquacreds@localhost:5432/aquacreds"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str | None = "http://localhost:8333"
    s3_region: str = "auto"
    s3_bucket: str = "aquacreds-dev"
    s3_access_key_id: str = "aquadev"
    s3_secret_access_key: SecretStr = SecretStr("aquadev-secret")
    # Endpoint used in presigned URLs handed to clients (browser/mobile). Inside Docker the API
    # reaches storage as http://s3:8333, but clients need a host-reachable address.
    s3_public_endpoint_url: str | None = None
    upload_max_bytes: int = 25 * 1024 * 1024
    upload_url_ttl_s: int = 900
    capture_nonce_ttl_min: int = 15  # a photo must be captured within this window

    jwt_secret: SecretStr = SecretStr("dev-only-change-me")
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 30
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Project boundary rules (MVP limits; see IMPLEMENTATION_PLAN Phase 2)
    project_min_area_ha: float = 0.5
    project_max_area_ha: float = 5000.0
    # Minimum shared area (ha) for two boundaries to count as overlapping (ignores edge-touching).
    project_overlap_tolerance_ha: float = 0.01

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def assert_production_safe(self) -> None:
        """Refuse to start in production with development defaults."""
        if self.app_env != "production":
            return
        problems = []
        if (
            self.jwt_secret.get_secret_value() == "dev-only-change-me"
            or len(self.jwt_secret.get_secret_value()) < 32
        ):
            problems.append("JWT_SECRET must be set to a random value of at least 32 characters")
        if self.s3_secret_access_key.get_secret_value() == "aquadev-secret":
            problems.append("S3_SECRET_ACCESS_KEY is still the development default")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
