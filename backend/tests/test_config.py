"""Startup safety: the app must refuse unsafe configuration outside local development."""

import ssl
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import build_connect_args

SAFE_PROD = {
    "ENVIRONMENT": "production",
    "AUTH_MODE": "cognito",
    "COGNITO_USER_POOL_ID": "us-east-1_abc",
    "COGNITO_CLIENT_ID": "client",
    "DB_SSL_MODE": "verify-full",
}


def make_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in (
        "ENVIRONMENT",
        "AUTH_MODE",
        "COGNITO_USER_POOL_ID",
        "COGNITO_CLIENT_ID",
        "DB_SSL_MODE",
        "JWKS_JSON",
        "DEV_AUTH_SECRET",
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_production_default_accepts_safe_config(monkeypatch):
    settings = make_settings(monkeypatch, **SAFE_PROD)
    assert settings.environment == "production"


def test_environment_defaults_to_production(monkeypatch):
    # A forgotten ENVIRONMENT must fail closed, not silently run in a permissive mode.
    with pytest.raises(ValidationError, match="COGNITO_USER_POOL_ID"):
        make_settings(monkeypatch)


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_dev_auth_is_refused_outside_local(monkeypatch, environment):
    with pytest.raises(ValidationError, match="AUTH_MODE=dev"):
        make_settings(monkeypatch, **{**SAFE_PROD, "ENVIRONMENT": environment, "AUTH_MODE": "dev"})


def test_dev_secret_and_static_jwks_are_refused_outside_local(monkeypatch):
    with pytest.raises(ValidationError, match="DEV_AUTH_SECRET"):
        make_settings(monkeypatch, **SAFE_PROD, DEV_AUTH_SECRET="x")
    with pytest.raises(ValidationError, match="JWKS_JSON"):
        make_settings(monkeypatch, **SAFE_PROD, JWKS_JSON="{}")


def test_plaintext_database_is_refused_outside_local(monkeypatch):
    with pytest.raises(ValidationError, match="DB_SSL_MODE"):
        make_settings(monkeypatch, **{**SAFE_PROD, "DB_SSL_MODE": "require"})


def test_local_allows_dev_auth_and_plaintext_db(monkeypatch):
    settings = make_settings(monkeypatch, ENVIRONMENT="local", AUTH_MODE="dev")
    assert settings.auth_mode == "dev"
    assert settings.db_ssl_mode == "disable"


def test_pool_is_derived_from_the_connection_budget(monkeypatch):
    settings = make_settings(monkeypatch, ENVIRONMENT="local")
    per_pod = settings.db_connection_budget // settings.max_api_pods
    main = settings.effective_pool_size + settings.effective_max_overflow
    # The export pool is carved out of the same per-pod budget.
    assert main + settings.export_pool_size == per_pod
    # All pods at the HPA maximum stay inside the budget.
    assert settings.max_api_pods * per_pod <= settings.db_connection_budget


def test_explicit_pool_values_override_the_budget(monkeypatch):
    settings = make_settings(
        monkeypatch, ENVIRONMENT="local", DB_POOL_SIZE="3", DB_MAX_OVERFLOW="1"
    )
    assert (settings.effective_pool_size, settings.effective_max_overflow) == (3, 1)


def test_tls_modes(monkeypatch):
    plain = make_settings(monkeypatch, ENVIRONMENT="local")
    assert build_connect_args(plain) == {}

    require = make_settings(monkeypatch, ENVIRONMENT="local", DB_SSL_MODE="require")
    ctx = build_connect_args(require)["ssl"]
    assert ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False

    bundle = Path(__file__).resolve().parent.parent / "certs" / "rds-global-bundle.pem"
    verify = make_settings(
        monkeypatch,
        **SAFE_PROD,
        DB_SSL_CA_FILE=str(bundle),
    )
    ctx = build_connect_args(verify)["ssl"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    # The RDS bundle is loaded as the trust store.
    assert ctx.cert_store_stats()["x509_ca"] >= 100
