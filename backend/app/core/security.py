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
