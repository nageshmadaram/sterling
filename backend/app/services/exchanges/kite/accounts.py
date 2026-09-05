"""
Multi-tenant Kite account store.

Per-user credential + session persistence (SQLite ``kite_accounts`` table,
write-through over an in-memory dict — same proven pattern as
``exchange_account_store``, but scoped by ``user_id`` and with secrets encrypted
at rest via :mod:`app.core.security`).

Secrets (``api_secret``, ``access_token``) are encrypted; ``api_key`` is stored in
the clear (it is semi-public — it appears in the login URL). Responses never leak
raw secrets, only a masked hint.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.core.security import decrypt, encrypt

from . import session as _session
from .models import KiteAccountCreate, KiteAccountResponse, KiteAccountUpdate

log = get_logger(__name__)

_accounts: Dict[str, "_Account"] = {}
_loaded = False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return "KITE-" + uuid.uuid4().hex[:10].upper()


@dataclass
class _Account:
    id: str
    user_id: str
    label: str
    api_key: str = ""
    api_secret_enc: str = ""
    access_token_enc: str = ""
    refresh_token_enc: str = ""
    public_token: str = ""
    kite_user_id: str = ""
    user_name: str = ""
    is_paper: bool = True
    is_active: bool = False
    last_login_at_ms: Optional[int] = None
    token_expires_at_ms: Optional[int] = None
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    # ── derived ──
    @property
    def api_secret(self) -> str:
        return decrypt(self.api_secret_enc)

    @property
    def access_token(self) -> str:
        return decrypt(self.access_token_enc)

    @property
    def refresh_token(self) -> str:
        return decrypt(self.refresh_token_enc)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret_enc)

    @property
    def connected(self) -> bool:
        return bool(self.access_token_enc)

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token_enc)

    @property
    def token_expires_at(self) -> Optional[int]:
        """When this access_token dies, in epoch-ms — ``None`` when unknowable.

        Prefers the stamp recorded at login. Rows written before expiry tracking
        existed carry no stamp, so we derive one from ``last_login_at_ms``: Kite's
        reset is a wall-clock event, so the boundary is reconstructible from the
        login time alone. Only a row with neither is genuinely unknown, and those
        fall through to a network validation rather than being trusted.
        """
        if self.token_expires_at_ms:
            return self.token_expires_at_ms
        if self.last_login_at_ms:
            return _session.token_expiry_ms(self.last_login_at_ms)
        return None

    @property
    def token_is_live(self) -> bool:
        """A stored token still inside its validity window. Says nothing about
        whether Kite would *accept* it (revoked, logged out elsewhere) — proving
        that is :func:`app.services.exchanges.kite.auth.ensure_session`'s job."""
        if not self.access_token_enc:
            return False
        expires = self.token_expires_at
        if expires is None:
            return False                      # unknown ⇒ not provable ⇒ validate
        return not _session.is_expired(expires)

    def api_key_hint(self) -> str:
        if not self.api_key or len(self.api_key) < 4:
            return "****"
        return "****" + self.api_key[-4:]


# ─── SQLite persistence ───────────────────────────────────────────────────────
def _init_table() -> None:
    from app.services import db
    if not db._available:
        return
    try:
        with db._conn() as c:
            c.execute("BEGIN")
            try:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS kite_accounts (
                        id               TEXT PRIMARY KEY,
                        user_id          TEXT NOT NULL,
                        label            TEXT NOT NULL DEFAULT 'My Kite',
                        api_key          TEXT NOT NULL DEFAULT '',
                        api_secret_enc   TEXT NOT NULL DEFAULT '',
                        access_token_enc TEXT NOT NULL DEFAULT '',
                        refresh_token_enc TEXT NOT NULL DEFAULT '',
                        public_token     TEXT NOT NULL DEFAULT '',
                        kite_user_id     TEXT NOT NULL DEFAULT '',
                        user_name        TEXT NOT NULL DEFAULT '',
                        is_paper         INTEGER NOT NULL DEFAULT 1,
                        is_active        INTEGER NOT NULL DEFAULT 0,
                        last_login_at_ms INTEGER,
                        token_expires_at_ms INTEGER,
                        created_at_ms    INTEGER NOT NULL,
                        updated_at_ms    INTEGER NOT NULL
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS ix_kite_accounts_user ON kite_accounts(user_id)")
                # Idempotent migrations for DBs created before these columns existed.
                # Each ALTER is tried independently so one already-applied column
                # cannot mask a genuinely missing later one.
                for _ddl in (
                    "ALTER TABLE kite_accounts ADD COLUMN refresh_token_enc TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE kite_accounts ADD COLUMN token_expires_at_ms INTEGER",
                    "ALTER TABLE kite_accounts ADD COLUMN user_name TEXT NOT NULL DEFAULT ''",
                ):
                    try:
                        c.execute(_ddl)
                    except Exception as _exc:
                        log.debug("suppressed: %s", _exc)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
    except Exception as exc:
        log.warning("kite_accounts table init failed: %s", exc)


def _persist(a: _Account) -> None:
    from app.services import db
    if not db._available:
        return
    try:
        with db._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO kite_accounts
                    (id, user_id, label, api_key, api_secret_enc, access_token_enc,
                     refresh_token_enc, public_token, kite_user_id, user_name, is_paper,
                     is_active, last_login_at_ms, token_expires_at_ms, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                a.id, a.user_id, a.label, a.api_key, a.api_secret_enc, a.access_token_enc,
                a.refresh_token_enc, a.public_token, a.kite_user_id, a.user_name,
                int(a.is_paper), int(a.is_active), a.last_login_at_ms, a.token_expires_at_ms,
                a.created_at_ms, a.updated_at_ms,
            ))
    except Exception as exc:
        log.warning("kite_accounts persist failed for %s: %s", a.id, exc)


def _delete_db(account_id: str) -> None:
    from app.services import db
    if not db._available:
        return
    try:
        with db._conn() as c:
            c.execute("DELETE FROM kite_accounts WHERE id = ?", (account_id,))
    except Exception as exc:
        log.warning("kite_accounts delete failed: %s", exc)


def _load_from_db() -> List[_Account]:
    from app.services import db
    if not db._available:
        return []
    try:
        with db._conn() as c:
            rows = c.execute("SELECT * FROM kite_accounts").fetchall()
        out = []
        for r in rows:
            out.append(_Account(
                id=r["id"], user_id=r["user_id"], label=r["label"], api_key=r["api_key"],
                api_secret_enc=r["api_secret_enc"], access_token_enc=r["access_token_enc"],
                refresh_token_enc=(r["refresh_token_enc"] if "refresh_token_enc" in r.keys() else ""),
                public_token=r["public_token"], kite_user_id=r["kite_user_id"],
                user_name=(r["user_name"] if "user_name" in r.keys() else ""),
                is_paper=bool(r["is_paper"]), is_active=bool(r["is_active"]),
                last_login_at_ms=r["last_login_at_ms"],
                token_expires_at_ms=(r["token_expires_at_ms"]
                                     if "token_expires_at_ms" in r.keys() else None),
                created_at_ms=r["created_at_ms"], updated_at_ms=r["updated_at_ms"],
            ))
        return out
    except Exception as exc:
        log.warning("kite_accounts load failed: %s", exc)
        return []


def bootstrap() -> None:
    global _loaded
    if _loaded:
        return
    _init_table()
    for a in _load_from_db():
        _accounts[a.id] = a
    _loaded = True


# ─── CRUD (user-scoped) ───────────────────────────────────────────────────────
def add(user_id: str, data: KiteAccountCreate) -> _Account:
    first_for_user = not any(a.user_id == user_id for a in _accounts.values())
    a = _Account(
        id=_new_id(), user_id=user_id, label=data.label or "My Kite",
        api_key=data.api_key, api_secret_enc=encrypt(data.api_secret),
        is_paper=data.is_paper, is_active=first_for_user,  # first account auto-active
    )
    _accounts[a.id] = a
    _persist(a)
    return a


def get(user_id: str, account_id: str) -> Optional[_Account]:
    a = _accounts.get(account_id)
    return a if a and a.user_id == user_id else None


def list_accounts(user_id: str) -> List[_Account]:
    return [a for a in _accounts.values() if a.user_id == user_id]


def all_accounts() -> List[_Account]:
    """Every stored account across all tenants. For background workers that must
    tend sessions with no request (and therefore no user) in scope."""
    return list(_accounts.values())


def update(user_id: str, account_id: str, data: KiteAccountUpdate) -> Optional[_Account]:
    a = get(user_id, account_id)
    if not a:
        return None
    if data.label is not None:
        a.label = data.label
    if data.api_key is not None:
        a.api_key = data.api_key
    if data.api_secret is not None:
        a.api_secret_enc = encrypt(data.api_secret)
    if data.is_paper is not None:
        a.is_paper = data.is_paper
    a.updated_at_ms = _now_ms()
    _persist(a)
    return a


def delete(user_id: str, account_id: str) -> bool:
    a = get(user_id, account_id)
    if not a:
        return False
    was_active = a.is_active
    del _accounts[account_id]
    _delete_db(account_id)
    if was_active:
        remaining = list_accounts(user_id)
        if remaining:
            remaining[0].is_active = True
            remaining[0].updated_at_ms = _now_ms()
            _persist(remaining[0])
    return True


def set_active(user_id: str, account_id: str) -> Optional[_Account]:
    target = get(user_id, account_id)
    if not target:
        return None
    for a in list_accounts(user_id):
        want = (a.id == account_id)
        if a.is_active != want:
            a.is_active = want
            a.updated_at_ms = _now_ms()
            _persist(a)
    return get(user_id, account_id)


def get_active(user_id: str) -> Optional[_Account]:
    return next((a for a in _accounts.values() if a.user_id == user_id and a.is_active), None)


def find_by_kite_user_id(kite_user_id: str) -> Optional[_Account]:
    """Resolve a Zerodha user id (carried in order postbacks) → stored account.

    Order postbacks identify the trader by their Kite ``user_id``, not our app
    ``user_id``; this maps it back so the update can be routed to the right tenant.
    """
    if not kite_user_id:
        return None
    return next((a for a in _accounts.values() if a.kite_user_id == kite_user_id), None)


def save_session(user_id: str, account_id: str, *, access_token: str,
                 refresh_token: str = "", public_token: str = "",
                 kite_user_id: str = "", user_name: str = "",
                 expires_at_ms: Optional[int] = None) -> Optional[_Account]:
    a = get(user_id, account_id)
    if not a:
        return None
    a.access_token_enc = encrypt(access_token)
    # Keep the existing refresh_token if the caller didn't supply a new one (a
    # token refresh may not return one); only re-encrypt when present.
    if refresh_token:
        a.refresh_token_enc = encrypt(refresh_token)
    a.public_token = public_token
    a.kite_user_id = kite_user_id or a.kite_user_id
    # Persisted so /status can name the trader without spending a /user/profile
    # call on every poll.
    a.user_name = user_name or a.user_name
    a.last_login_at_ms = _now_ms()
    # Stamp the validity window now, so every later "is this usable?" question is
    # answerable locally instead of costing a round-trip to Kite.
    a.token_expires_at_ms = expires_at_ms or _session.token_expiry_ms(a.last_login_at_ms)
    a.updated_at_ms = a.last_login_at_ms
    mark_validated(a.id)          # freshly issued by Kite ⇒ known good right now
    _persist(a)
    return a


def clear_session(user_id: str, account_id: str) -> Optional[_Account]:
    a = get(user_id, account_id)
    if not a:
        return None
    a.access_token_enc = ""
    a.public_token = ""
    a.token_expires_at_ms = None
    a.updated_at_ms = _now_ms()
    _validated_at.pop(a.id, None)
    _persist(a)
    return a


# ─── Validation cache ─────────────────────────────────────────────────────────
# A stored token being inside its window does not prove Kite still honours it (it
# could have been revoked, or the user logged out on another device). Proving that
# costs a /user/profile call, so we remember when we last proved it. In-memory on
# purpose: a process restart should re-prove once rather than trust a stamp
# written by a process that may have been down across the 06:00 IST reset.
_validated_at: Dict[str, int] = {}


def mark_validated(account_id: str) -> None:
    """Record that Kite accepted this account's token just now."""
    _validated_at[account_id] = _now_ms()


def validated_age_ms(account_id: str) -> Optional[int]:
    """How long ago Kite last accepted this token — ``None`` if never proven."""
    at = _validated_at.get(account_id)
    return None if at is None else _now_ms() - at


def set_identity(user_id: str, account_id: str, *, user_name: str = "",
                 kite_user_id: str = "") -> Optional[_Account]:
    """Record who this session belongs to, outside the login handshake — used to
    backfill accounts that connected before the identity was persisted."""
    a = get(user_id, account_id)
    if not a:
        return None
    changed = False
    if user_name and user_name != a.user_name:
        a.user_name, changed = user_name, True
    if kite_user_id and kite_user_id != a.kite_user_id:
        a.kite_user_id, changed = kite_user_id, True
    if changed:
        a.updated_at_ms = _now_ms()
        _persist(a)
    return a


def forget_validation(account_id: str) -> None:
    """Drop the proof, forcing the next check to ask Kite. For tokens adopted from
    outside the login handshake (e.g. seeded from the environment), where we have
    no evidence Kite ever accepted them."""
    _validated_at.pop(account_id, None)


def to_response(a: _Account) -> KiteAccountResponse:
    return KiteAccountResponse(
        id=a.id, user_id=a.user_id, label=a.label, api_key_hint=a.api_key_hint(),
        has_credentials=a.has_credentials, is_paper=a.is_paper, is_active=a.is_active,
        connected=a.connected, has_refresh_token=a.has_refresh_token,
        kite_user_id=a.kite_user_id or None, user_name=a.user_name or None,
        last_login_at_ms=a.last_login_at_ms,
        token_expires_at_ms=a.token_expires_at,
        created_at_ms=a.created_at_ms, updated_at_ms=a.updated_at_ms,
    )


def build_client(a: _Account):
    """Construct a fresh KiteClient from a stored account (secrets decrypted
    in-memory). Prefer :func:`acquire_client` on hot paths — a fresh client has an
    empty instrument cache and no connection pool, so building one per request
    re-downloads the ~80k-row instrument dump and re-does the TLS handshake."""
    from .client import KiteClient
    client = KiteClient(
        api_key=a.api_key, api_secret=a.api_secret, access_token=a.access_token,
        is_paper=a.is_paper,
    )
    client._account_id = str(a.id)
    client._kite_user_id = str(a.kite_user_id or "")
    return client


# ─── Cached clients (warm instrument dump + connection pool, per account) ──────
# One live KiteClient per account, reused across requests so its InstrumentCache
# (1h TTL) and httpx connection pool stay warm. Rebuilt only when credentials or
# the access token rotate — validated against the *encrypted* fields so the hot
# path never decrypts. This is the fix for instrument search taking minutes
# (every request used to rebuild the client and re-fetch the full dump).
_client_cache: Dict[str, Tuple[str, str, "object"]] = {}  # id -> (api_key, token_enc, client)


async def acquire_client(a: _Account):
    """Return a warm KiteClient for the account, reusing its instrument cache and
    connection pool. Rebuilt only when api_key / access token change."""
    cached = _client_cache.get(a.id)
    if cached is not None and cached[0] == a.api_key and cached[1] == a.access_token_enc:
        return cached[2]
    if cached is not None:                      # credentials rotated — drop the stale client
        try:
            await cached[2].close()
        except Exception as _exc:# noqa: BLE001
            log.debug("suppressed: %s", _exc)
    client = build_client(a)
    _client_cache[a.id] = (a.api_key, a.access_token_enc, client)
    return client


async def release_client(account_id: str) -> None:
    """Drop and close a cached client (account deleted / switched / logged out)."""
    cached = _client_cache.pop(account_id, None)
    if cached is not None:
        try:
            await cached[2].close()
        except Exception as _exc:# noqa: BLE001
            log.debug("suppressed: %s", _exc)


def clear() -> None:
    """Test hook — wipe in-memory state into a 'loaded & empty' state.

    Sets ``_loaded=True`` so a subsequent app-startup ``bootstrap()`` is a no-op and
    never reloads stale rows from a shared test DB (kite has no default account to
    seed, so there is nothing to bootstrap in tests). Keeps the suite deterministic
    regardless of whether another test has flipped ``db._available`` on."""
    global _loaded
    _accounts.clear()
    _validated_at.clear()
    _loaded = True
