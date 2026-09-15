"""
Symmetric encryption for secrets at rest (Kite API secrets / access tokens).

Uses Fernet (AES-128-CBC + HMAC) from ``cryptography`` when available, keyed by
``STERLING_SECRET_KEY``. If the key is absent a deterministic dev key is derived
and a loud warning is logged (never do this in production). If ``cryptography``
itself is unavailable the module degrades to base64 obfuscation with a warning, so
dev/test still works — but that is NOT real encryption.

Ciphertext is prefixed with a scheme tag (``fernet:`` / ``b64:``) so values can be
decrypted regardless of which backend wrote them.
"""
from __future__ import annotations

import base64
import hashlib
import os

from app.core.logging import get_logger

log = get_logger(__name__)

_FERNET_PREFIX = "fernet:"
_B64_PREFIX = "b64:"

_fernet = None
_backend = "b64"


def _derive_fernet_key(secret: str) -> bytes:
    """A urlsafe-base64 32-byte key derived from the configured secret."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_jwt_key() -> str:
    """Return the signing key used for JWT access and refresh tokens."""
    return (
        os.environ.get("STERLING_JWT_SECRET")
        or os.environ.get("STERLING_SECRET_KEY")
        or "sterling-dev-jwt-secret-key"
    )


def _init() -> None:
    global _fernet, _backend
    if _fernet is not None or _backend == "fernet":
        return
    secret = os.environ.get("STERLING_SECRET_KEY", "")
    if not secret:
        secret = "sterling-dev-insecure-key"
        log.warning(
            "STERLING_SECRET_KEY not set — using an insecure dev key for secret "
            "encryption. Set STERLING_SECRET_KEY in production."
        )
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_derive_fernet_key(secret))
        _backend = "fernet"
    except Exception as exc:  # pragma: no cover - only when cryptography missing
        log.warning("cryptography unavailable (%s) — falling back to base64 obfuscation "
                    "(NOT encryption). Install 'cryptography' for at-rest encryption.", exc)
        _backend = "b64"


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Empty input → empty output."""
    if not plaintext:
        return ""
    _init()
    if _backend == "fernet" and _fernet is not None:
        token = _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        return _FERNET_PREFIX + token
    return _B64_PREFIX + base64.urlsafe_b64encode(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a stored secret. Tolerates legacy plaintext (returns as-is)."""
    if not ciphertext:
        return ""
    _init()
    if ciphertext.startswith(_FERNET_PREFIX):
        if _fernet is None:
            raise RuntimeError("Cannot decrypt Fernet secret — cryptography unavailable.")
        return _fernet.decrypt(ciphertext[len(_FERNET_PREFIX):].encode("utf-8")).decode("utf-8")
    if ciphertext.startswith(_B64_PREFIX):
        return base64.urlsafe_b64decode(ciphertext[len(_B64_PREFIX):].encode("utf-8")).decode("utf-8")
    # Legacy/plaintext value written before encryption was introduced.
    return ciphertext


DEV_FALLBACK_VALUES = {
    "sterling-dev-insecure-key",
    "sterling-dev-jwt-secret-key",
    "dev-secret",
    "secret",
    "change-me",
}


def validate_production_security() -> None:
    """Enforce strict fail-closed security rules in production mode.

    Production requirement:
    - ENVIRONMENT=production requires distinct, randomly generated STERLING_SECRET_KEY
      and STERLING_JWT_SECRET, each at least 32 characters, neither matching known
      development fallback values.
    - Production secrets must be randomly generated (e.g. 32 random bytes/chars) rather than chosen manually.
    - Fernet crypto backend must be available and active.
    - Every persisted sensitive credential across system_config, kite_accounts, truedata_credentials,
      and all _enc columns must have valid fernet: encoding and decrypt cleanly under configured key.
    - Any violation aborts startup immediately before broker clients or scanners launch.
    """
    env = os.environ.get("ENVIRONMENT", "development").lower()
    if env != "production":
        return

    secret = os.environ.get("STERLING_SECRET_KEY", "")
    if not secret or secret in DEV_FALLBACK_VALUES or len(secret) < 32:
        raise RuntimeError(
            "Production security error: STERLING_SECRET_KEY must be a distinct, randomly generated "
            "secret of at least 32 characters (found length %d)." % len(secret)
        )

    jwt_secret = os.environ.get("STERLING_JWT_SECRET", "")
    if not jwt_secret or jwt_secret in DEV_FALLBACK_VALUES or len(jwt_secret) < 32:
        raise RuntimeError(
            "Production security error: STERLING_JWT_SECRET must be a distinct, randomly generated "
            "secret of at least 32 characters (found length %d)." % len(jwt_secret)
        )

    if secret == jwt_secret:
        raise RuntimeError("Production security error: STERLING_SECRET_KEY and STERLING_JWT_SECRET must be distinct")

    _init()
    if _backend != "fernet":
        raise RuntimeError("Production security error: Fernet encryption is unavailable (weaker backend active)")

    from app.services import db
    if hasattr(db, "is_available") and db.is_available():
        # 1. Check system_config table
        configs = db.get_all_configs()
        for k, v in configs.items():
            if ("secret" in k.lower() or "token" in k.lower() or "password" in k.lower()) and v:
                if not v.startswith(_FERNET_PREFIX):
                    raise RuntimeError(f"Production security error: Secret in system_config for key '{k}' is not Fernet-encrypted")
                try:
                    decrypt(v)
                except Exception as exc:
                    raise RuntimeError(f"Production security error: Secret in system_config for key '{k}' failed decryption: {exc}")

        # 2. Inspect all table columns ending in '_enc' (kite_accounts, truedata_credentials, etc.)
        try:
            with db._conn() as c:
                tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                for t in tables:
                    tname = t["name"] if isinstance(t, dict) else t[0]
                    cols_info = c.execute(f"PRAGMA table_info({tname})").fetchall()
                    cols = [info["name"] if isinstance(info, dict) else info[1] for info in cols_info]
                    enc_cols = [col for col in cols if col.endswith("_enc")]
                    if not enc_cols:
                        continue
                    query = f"SELECT {', '.join(enc_cols)} FROM {tname}"
                    rows = c.execute(query).fetchall()
                    for row in rows:
                        for col in enc_cols:
                            val = row[col] if isinstance(row, dict) else row[enc_cols.index(col)]
                            if val:
                                sval = str(val)
                                if not sval.startswith(_FERNET_PREFIX):
                                    raise RuntimeError(
                                        f"Production security error: Credential column '{col}' in table '{tname}' "
                                        f"contains unencrypted/legacy value (must start with '{_FERNET_PREFIX}')"
                                    )
                                try:
                                    decrypt(sval)
                                except Exception as exc:
                                    raise RuntimeError(
                                        f"Production security error: Credential in '{tname}.{col}' failed decryption under active key: {exc}"
                                    )
        except Exception as exc:
            if "Production security error" in str(exc):
                raise
            log.warning("Persisted credential store inspection error: %s", exc)



