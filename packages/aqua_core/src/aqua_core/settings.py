"""Typed runtime configuration, read from environment variables (and `.env` in development)."""

import base64
import binascii
import ipaddress
import re
from datetime import date
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Foundry/Hardhat's well-known development mnemonic: its keys are public, so production refuses it.
ANVIL_DEV_MNEMONIC = "test test test test test test test test test test test junk"
# Fernet key used for MFA secrets in development/test only (derived, so secret scanners see no key
# literal). Production refuses it.
DEV_MFA_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"aquacreds-dev-only-mfa-key-32by!").decode()

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(second|minute|hour|day)\s*$")
_RATE_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_rate(rate: str) -> tuple[int, int]:
    """ "10/minute" → (10, 60): at most `count` requests per window of `seconds`."""
    match = _RATE_RE.match(rate)
    if match is None or int(match.group(1)) < 1:
        raise ValueError(f"invalid rate {rate!r}; expected '<n>/<second|minute|hour|day>'")
    return int(match.group(1)), _RATE_UNITS[match.group(2)]


def _is_fernet_key(key: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(key.encode())) == 32
    except (binascii.Error, ValueError):
        return False


# Must match aqua_geo.sentinel2.PROVIDERS (aqua_core does not import aqua_geo; a test checks it).
GEO_STAC_PROVIDER_NAMES = ("planetary-computer", "earth-search", "earth-search-c1")


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


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
    # Public web origin used in retirement-certificate links and QR codes (no trailing slash).
    public_base_url: str = "http://localhost:3000"

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
    # Sentinel-2 STAC catalogues in fallback order (comma-separated). The next one is tried on
    # timeouts, HTTP 5xx/429 and auth errors (401/403); see aqua_geo.fallback.
    geo_stac_providers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["planetary-computer", "earth-search"]
    )
    # Sentinel-1 RTC (Planetary Computer `sentinel-1-rtc`) during ingestion: acquisitions in the
    # monsoon months, and within ±window days of a Sentinel-2 scene whose clear fraction is below
    # the floor, are reduced to per-polygon backscatter statistics (table `sar_observations`).
    geo_s1_enabled: bool = True
    geo_s1_monsoon_months: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [6, 7, 8, 9]
    )
    geo_s1_clear_floor: float = Field(default=0.3, ge=0, le=1)
    geo_s1_window_days: int = Field(default=6, ge=0, le=30)
    # Sentinel-1 backscatter-drop alerts (aqua_worker.alerts.detect_sar_backscatter_drop). A
    # HEURISTIC, off by default: VH dB P50 below the project's own same-season (monsoon months)
    # baseline from earlier years, same relative orbit, by `drop_db` on `consecutive` acquisitions
    # in a row. Severity is never above medium; it needs optical or field confirmation.
    geo_sar_alerts_enabled: bool = False
    geo_sar_alert_drop_db: float = Field(default=3.0, gt=0, le=20)
    geo_sar_alert_consecutive: int = Field(default=3, ge=1, le=20)
    geo_sar_alert_min_history: int = Field(default=6, ge=1, le=200)
    geo_sar_alert_min_valid_fraction: float = Field(default=0.6, ge=0, le=1)

    # ---------------------------------------------------------------- blockchain (Phase 6)
    chain_rpc_url: str = "http://localhost:8545"
    chain_id: int = 31337  # Anvil; Polygon Amoy = 80002
    chain_rpc_timeout_s: float = 20.0
    chain_registry_address: str | None = None
    chain_credit_address: str | None = None
    chain_marketplace_address: str | None = None
    chain_usdc_address: str | None = None
    # AquaForwardFund (Phase 7, reverse-auction forward funding). Optional: when unset the indexer
    # skips it and the forward-fund API answers 503.
    chain_forward_fund_address: str | None = None
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
    chain_index_interval_s: float = Field(default=30.0, gt=0)  # beat: aqua.chain.index_events

    # ---------------------------------------------------------------- worker ops (Phase 9)
    # A run still `running` this long after it started is marked failed by aqua.geo.reap_stale_runs.
    # Must exceed the longest Celery hard time limit (6 h 5 min for backfill/nightly ingestion).
    geo_stale_run_timeout_min: int = Field(default=8 * 60, ge=60)

    # ---------------------------------------------------------------- auth hardening (Phase 9)
    # Fernet key(s) encrypting TOTP secrets at rest, comma-separated: the first encrypts, all
    # decrypt (key rotation). Generate: python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"
    mfa_encryption_key: SecretStr = SecretStr(DEV_MFA_ENCRYPTION_KEY)
    # Staff (verifier / registry_admin) must enrol TOTP before using staff-only endpoints.
    # None = required in production only.
    mfa_required_for_staff: bool | None = None
    mfa_issuer: str = "AquaCreds"
    mfa_token_ttl_s: int = Field(default=300, ge=30, le=900)
    # Fixed-window rate limits on the auth endpoints, stored in Valkey ("<n>/<unit>").
    rate_limit_enabled: bool = True
    rate_limit_login_ip: str = "10/minute"
    rate_limit_login_account: str = "5/minute"
    rate_limit_register_ip: str = "5/minute"
    rate_limit_refresh_ip: str = "30/minute"
    rate_limit_mfa_ip: str = "10/minute"
    rate_limit_mfa_account: str = "5/minute"
    # Reverse proxies (IPs or CIDRs, comma-separated) whose X-Forwarded-For is trusted for the
    # client address, e.g. the Docker network Caddy runs on. Empty = use the TCP peer address.
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator(
        "cors_origins",
        "trusted_proxy_ips",
        "geo_stac_providers",
        "geo_s1_monsoon_months",
        mode="before",
    )
    @classmethod
    def _split_lists(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("geo_stac_providers")
    @classmethod
    def _known_stac_providers(cls, value: list[str]) -> list[str]:
        unknown = [name for name in value if name not in GEO_STAC_PROVIDER_NAMES]
        if unknown or not value or len(set(value)) != len(value):
            raise ValueError(
                f"GEO_STAC_PROVIDERS must list distinct names from {GEO_STAC_PROVIDER_NAMES}"
            )
        return value

    @field_validator("geo_s1_monsoon_months")
    @classmethod
    def _valid_months(cls, value: list[int]) -> list[int]:
        if any(not 1 <= m <= 12 for m in value):
            raise ValueError("GEO_S1_MONSOON_MONTHS must be month numbers 1-12")
        return sorted(set(value))

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _valid_networks(cls, value: list[str]) -> list[str]:
        for item in value:
            ipaddress.ip_network(item, strict=False)  # raises ValueError on garbage
        return value

    @field_validator(
        "rate_limit_login_ip",
        "rate_limit_login_account",
        "rate_limit_register_ip",
        "rate_limit_refresh_ip",
        "rate_limit_mfa_ip",
        "rate_limit_mfa_account",
    )
    @classmethod
    def _valid_rate(cls, value: str) -> str:
        parse_rate(value)
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

    @property
    def staff_mfa_required(self) -> bool:
        if self.mfa_required_for_staff is None:
            return self.app_env == "production"
        return self.mfa_required_for_staff

    @property
    def _configured_mfa_keys(self) -> list[str]:
        raw = self.mfa_encryption_key.get_secret_value()
        return [k.strip() for k in raw.split(",") if k.strip()]

    @property
    def mfa_encryption_keys(self) -> list[str]:
        """Fernet keys, first = encryption key. An empty value outside production falls back to
        the development key (production refuses both)."""
        keys = self._configured_mfa_keys
        if not keys and self.app_env != "production":
            return [DEV_MFA_ENCRYPTION_KEY]
        return keys

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
        mfa_keys = self._configured_mfa_keys
        if not mfa_keys:
            problems.append("MFA_ENCRYPTION_KEY must be set (Fernet key)")
        elif DEV_MFA_ENCRYPTION_KEY in mfa_keys:
            problems.append("MFA_ENCRYPTION_KEY is the development default")
        elif not all(_is_fernet_key(k) for k in mfa_keys):
            problems.append("MFA_ENCRYPTION_KEY must be url-safe base64 Fernet key(s) (32 bytes)")
        if self.mfa_required_for_staff is False:
            problems.append("MFA_REQUIRED_FOR_STAFF must not be disabled in production")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
