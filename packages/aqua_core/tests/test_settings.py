import pytest

from aqua_core.settings import Settings


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://app.example.org ,")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000", "https://app.example.org"]


def test_secrets_are_not_leaked_in_repr() -> None:
    settings = Settings(_env_file=None, jwt_secret="super-secret-value")
    assert "super-secret-value" not in repr(settings)


def test_production_refuses_dev_defaults() -> None:
    settings = Settings(_env_file=None, app_env="production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        settings.assert_production_safe()


def test_production_accepts_strong_config() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 48,
        s3_secret_access_key="real-r2-secret",
    )
    settings.assert_production_safe()


def test_development_allows_defaults() -> None:
    Settings(_env_file=None).assert_production_safe()
