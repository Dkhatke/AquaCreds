import base64

import pytest
from pydantic import ValidationError

from aqua_core.settings import ANVIL_DEV_MNEMONIC, DEV_MFA_ENCRYPTION_KEY, Settings

# BIP-39 test vector (no funds); only used to satisfy the "mnemonic configured" check.
TEST_VECTOR_MNEMONIC = " ".join(["abandon"] * 11 + ["about"])
PROD_MFA_KEY = base64.urlsafe_b64encode(b"p" * 32).decode()


def _prod(**overrides: object) -> Settings:
    options: dict[str, object] = {
        "app_env": "production",
        "jwt_secret": "x" * 48,
        "s3_secret_access_key": "real-r2-secret",
        "relayer_mnemonic": TEST_VECTOR_MNEMONIC,
        "mfa_encryption_key": PROD_MFA_KEY,
        **overrides,
    }
    return Settings(_env_file=None, **options)  # type: ignore[arg-type]


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://app.example.org ,")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000", "https://app.example.org"]


def test_secrets_are_not_leaked_in_repr() -> None:
    settings = Settings(
        _env_file=None, jwt_secret="super-secret-value", relayer_mnemonic=TEST_VECTOR_MNEMONIC
    )
    assert "super-secret-value" not in repr(settings)
    assert "abandon" not in repr(settings)


def test_production_refuses_dev_defaults() -> None:
    settings = Settings(_env_file=None, app_env="production")
    with pytest.raises(RuntimeError, match="JWT_SECRET") as exc:
        settings.assert_production_safe()
    assert "RELAYER_MNEMONIC must be set" in str(exc.value)


def test_production_refuses_the_public_anvil_mnemonic() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 48,
        s3_secret_access_key="real-r2-secret",
        relayer_mnemonic=f"  {ANVIL_DEV_MNEMONIC} ",
    )
    with pytest.raises(RuntimeError, match="public Anvil development mnemonic"):
        settings.assert_production_safe()


def test_production_accepts_strong_config() -> None:
    settings = _prod()
    settings.assert_production_safe()
    assert settings.staff_mfa_required  # default on in production


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("", "MFA_ENCRYPTION_KEY must be set"),
        (DEV_MFA_ENCRYPTION_KEY, "MFA_ENCRYPTION_KEY is the development default"),
        (f"{PROD_MFA_KEY},{DEV_MFA_ENCRYPTION_KEY}", "development default"),
        ("not-a-fernet-key", "url-safe base64 Fernet key"),
    ],
)
def test_production_refuses_missing_or_dev_mfa_key(key: str, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        _prod(mfa_encryption_key=key).assert_production_safe()


def test_production_refuses_disabling_staff_mfa() -> None:
    with pytest.raises(RuntimeError, match="MFA_REQUIRED_FOR_STAFF"):
        _prod(mfa_required_for_staff=False).assert_production_safe()


def test_empty_mfa_key_falls_back_to_dev_key_outside_production() -> None:
    assert Settings(_env_file=None, mfa_encryption_key="").mfa_encryption_keys == [
        DEV_MFA_ENCRYPTION_KEY
    ]
    configured = Settings(_env_file=None, mfa_encryption_key=f" {PROD_MFA_KEY} ,")
    assert configured.mfa_encryption_keys == [PROD_MFA_KEY]


def test_staff_mfa_default_off_outside_production() -> None:
    assert not Settings(_env_file=None).staff_mfa_required
    assert Settings(_env_file=None, mfa_required_for_staff=True).staff_mfa_required


def test_trusted_proxies_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.16.0.0/12, 10.0.0.5")
    assert Settings(_env_file=None).trusted_proxy_ips == ["172.16.0.0/12", "10.0.0.5"]


def test_development_allows_defaults() -> None:
    Settings(_env_file=None).assert_production_safe()


def test_registrar_and_verifier_must_use_different_keys() -> None:
    with pytest.raises(ValidationError, match="separation of duties"):
        Settings(_env_file=None, chain_registrar_index=4, chain_verifier_index=4)


def test_signer_indices_stay_below_org_wallet_range() -> None:
    with pytest.raises(ValidationError, match="below CHAIN_ORG_INDEX_OFFSET"):
        Settings(_env_file=None, chain_issuer_index=1000)


def test_chain_configured_needs_mnemonic_and_contracts() -> None:
    assert not Settings(_env_file=None).chain_configured
    settings = Settings(
        _env_file=None,
        relayer_mnemonic=TEST_VECTOR_MNEMONIC,
        chain_registry_address="0x" + "11" * 20,
        chain_credit_address="0x" + "22" * 20,
    )
    assert settings.chain_configured


def test_funder_uses_its_own_key() -> None:
    with pytest.raises(ValidationError, match="separation of duties"):
        Settings(_env_file=None, chain_funder_index=1)  # the registrar's index
    settings = Settings(_env_file=None)
    assert settings.chain_funder_index == 4
    assert settings.chain_org_wallet_top_up <= settings.chain_org_wallet_daily_cap
    assert settings.chain_org_wallet_daily_cap <= settings.chain_funder_daily_cap


def test_notification_email_is_off_by_default_and_checked_in_production() -> None:
    dev = Settings(_env_file=None)
    assert dev.notification_email_enabled is False
    assert dev.notification_email_backend == "console"
    _prod().assert_production_safe()  # email off: nothing else to configure

    cases = [
        ({}, "NOTIFICATION_EMAIL_BACKEND must be smtp"),
        ({"notification_email_backend": "smtp"}, "SMTP_HOST must be set"),
        (
            {
                "notification_email_backend": "smtp",
                "smtp_host": "smtp.example",
                "smtp_security": "none",
            },
            "SMTP_SECURITY=none",
        ),
        (
            {"notification_email_backend": "smtp", "smtp_host": "smtp.example"},
            "NOTIFICATION_EMAIL_FROM must be a real sender",
        ),
    ]
    for overrides, problem in cases:
        with pytest.raises(RuntimeError, match=problem):
            _prod(notification_email_enabled=True, **overrides).assert_production_safe()
    _prod(
        notification_email_enabled=True,
        notification_email_backend="smtp",
        smtp_host="smtp-relay.example",
        notification_email_from="AquaCreds <alerts@aquacreds.example>",
    ).assert_production_safe()
