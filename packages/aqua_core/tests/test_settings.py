import pytest
from pydantic import ValidationError

from aqua_core.settings import ANVIL_DEV_MNEMONIC, Settings

# BIP-39 test vector (no funds); only used to satisfy the "mnemonic configured" check.
TEST_VECTOR_MNEMONIC = " ".join(["abandon"] * 11 + ["about"])


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
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 48,
        s3_secret_access_key="real-r2-secret",
        relayer_mnemonic=TEST_VECTOR_MNEMONIC,
    )
    settings.assert_production_safe()


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
