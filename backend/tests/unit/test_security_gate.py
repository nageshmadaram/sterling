import os
import pytest
from app.core import security
from app.core.security import validate_production_security, encrypt
from app.services import db

VALID_SECRET_1 = "super-secret-key-1234567890-abcdef1234567890"
VALID_SECRET_2 = "jwt-secret-key-0987654321-fedcba0987654321"

def test_security_gate_dev_mode():
    os.environ["ENVIRONMENT"] = "development"
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

def test_security_gate_prod_uninitialized_db(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    monkeypatch.setattr(db, "_available", False)
    with pytest.raises(RuntimeError, match="Database store is uninitialized or unavailable for credential inspection"):
        validate_production_security()

def test_security_gate_prod_plaintext_system_config(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()
    db.set_config("telegram_bot_token", "unencrypted_raw_token")

    with pytest.raises(RuntimeError, match="Secret in system_config for key 'telegram_bot_token' is not Fernet-encrypted"):
        validate_production_security()

def test_security_gate_prod_b64_kite_account(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()
    with db._conn() as c:
        c.execute("INSERT INTO kite_accounts (id, user_id, api_secret_enc, created_at_ms, updated_at_ms) VALUES ('k1', 'u1', 'b64:c2VjcmV0', 100, 100)")

    with pytest.raises(RuntimeError, match="Credential column 'api_secret_enc' in table 'kite_accounts' contains unencrypted/legacy value"):
        validate_production_security()

def test_security_gate_prod_valid_fernet_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    # Ensure fernet initialized with VALID_SECRET_1
    security._fernet = None
    security._backend = "b64"
    security._init()

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()

    encrypted_secret = encrypt("my_real_broker_secret")
    assert encrypted_secret.startswith("fernet:")

    with db._conn() as c:
        c.execute("INSERT INTO kite_accounts (id, user_id, api_secret_enc, created_at_ms, updated_at_ms) VALUES ('k1', 'u1', ?, 100, 100)", (encrypted_secret,))

    # Should pass without error
    validate_production_security()
