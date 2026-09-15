import os
import pytest
from app.core.security import validate_production_security

VALID_SECRET_1 = "super-secret-key-1234567890-abcdef1234567890"
VALID_SECRET_2 = "jwt-secret-key-0987654321-fedcba0987654321"

def test_security_gate_dev_mode():
    os.environ["ENVIRONMENT"] = "development"
    # Should not raise any error in dev mode
    validate_production_security()

def test_security_gate_prod_missing_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("STERLING_SECRET_KEY", raising=False)
    monkeypatch.delenv("STERLING_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="STERLING_SECRET_KEY must be a distinct, randomly generated secret"):
        validate_production_security()

def test_security_gate_prod_short_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", "short_secret_16char")
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    with pytest.raises(RuntimeError, match="STERLING_SECRET_KEY must be a distinct, randomly generated secret of at least 32 characters"):
        validate_production_security()

def test_security_gate_prod_identical_secrets(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_1)
    with pytest.raises(RuntimeError, match="STERLING_SECRET_KEY and STERLING_JWT_SECRET must be distinct"):
        validate_production_security()

def test_security_gate_prod_dev_fallback_jwt(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", "sterling-dev-jwt-secret-key")
    with pytest.raises(RuntimeError, match="STERLING_JWT_SECRET must be a distinct, randomly generated secret"):
        validate_production_security()
