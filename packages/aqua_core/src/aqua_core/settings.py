"""Typed runtime configuration, read from environment variables (and `.env` in development)."""

from datetime import date
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Foundry/Hardhat's well-known development mnemonic: its keys are public, so production refuses it.
ANVIL_DEV_MNEMONIC = "test test test test test test test test test test test junk"


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

    # Satellite pipeline (worker `geo` queue; see IMPLEMENTATION_PLAN Phase 4 / RESEARCH_NOTES Q2)
    geo_backfill_start: date = date(2017, 1, 1)  # Sentinel-2 L2A coverage of India starts ~2017
    geo_nightly_hour_utc: int = Field(default=20, ge=0, le=23)  # 20:00 UTC = 01:30 IST
    geo_max_scene_cloud_pct: float = Field(default=80.0, gt=0, le=100)  # tile-level pre-filter
    geo_composite_max_cloud_pct: float = Field(default=40.0, gt=0, le=100)

    # ---------------------------------------------------------------- blockchain (Phase 6)
    chain_rpc_url: str = "http://localhost:8545"
    chain_id: int = 31337  # Anvil; Polygon Amoy = 80002
    chain_rpc_timeout_s: float = 20.0
    chain_registry_address: str | None = None
    chain_credit_address: str | None = None
    chain_marketplace_address: str | None = None
    chain_usdc_address: str | None = None
    chain_deploy_block: int = 0  # first block the indexer scans
    # BIP-39 mnemonic of the platform relayer. All signer and custodial org keys are derived from
    # it (BIP-44 m/44'/60'/0'/0/{index}); it never touches the database. Docker secret in prod.
    relayer_mnemonic: SecretStr = SecretStr("")
    # Separation of duties: each on-chain role signs with its own derived account. The contract
    # rejects a report approved by its submitter, and settings refuse registrar == verifier.
    chain_registrar_index: int = Field(default=1, ge=0)
    chain_verifier_index: int = Field(default=2, ge=0)
    chain_issuer_index: int = Field(default=3, ge=0)
    chain_org_index_offset: int = Field(default=1000, ge=0)  # org wallets: offset + n
    chain_confirmations: int = Field(default=3, ge=1)
    chain_gas_limit_cap: int = Field(default=3_000_000, gt=21_000)
    chain_gas_price_cap_gwei: float = Field(default=200.0, gt=0)
    chain_priority_fee_gwei: float = Field(default=1.5, ge=0)  # Amoy needs >= 25-30 gwei
    chain_index_batch_blocks: int = Field(default=2000, ge=1)
    chain_reorg_depth: int = Field(default=12, ge=0)  # indexer re-scans this many recent blocks

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _separate_chain_signers(self) -> "Settings":
        roles = {
            "CHAIN_REGISTRAR_INDEX": self.chain_registrar_index,
            "CHAIN_VERIFIER_INDEX": self.chain_verifier_index,
            "CHAIN_ISSUER_INDEX": self.chain_issuer_index,
        }
        if len(set(roles.values())) != len(roles):
            raise ValueError(
                f"chain signer indices must all differ (separation of duties): {roles}"
            )
        if any(index >= self.chain_org_index_offset for index in roles.values()):
            raise ValueError("chain signer indices must be below CHAIN_ORG_INDEX_OFFSET")
        return self

    @property
    def chain_configured(self) -> bool:
        """True when the relayer can sign and the core contracts are known."""
        return bool(
            self.relayer_mnemonic.get_secret_value().strip()
            and self.chain_registry_address
            and self.chain_credit_address
        )

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
        mnemonic = " ".join(self.relayer_mnemonic.get_secret_value().split())
        if not mnemonic:
            problems.append("RELAYER_MNEMONIC must be set (Docker secret)")
        elif mnemonic == ANVIL_DEV_MNEMONIC:
            problems.append("RELAYER_MNEMONIC is the public Anvil development mnemonic")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
