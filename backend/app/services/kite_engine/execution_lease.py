"""Cross-process ownership of one position's broker conversation.

``monitor._exiting`` and the protection guards serialise coroutines on ONE event
loop. That is enough while a single engine process runs, and it is exactly the
assumption that breaks the moment a second worker, a restarted service or a
manual script talks to the same account: two processes each pass their own
in-memory check, and both place a SELL. For a long option that is a duplicate
exit; for a futures position it is a naked short.

A lease is one row in SQLite taken under ``BEGIN IMMEDIATE``, so the winner is
decided by the database rather than by timing. It is deliberately NOT a general
mutex:

* **Only live positions take one.** A paper position has no broker to race for,
  and requiring a database for it would make simulation depend on storage.
* **A stale lease may be taken over.** The durable fence against a duplicate
  exit is the position's own ``exit_order_id``, which is persisted BEFORE the
  network call and survives a restart; the lease only closes the window in which
  two live processes are inside that same preparation at once. Refusing to ever
  take over would let one crashed worker strand a real position with no way to
  exit it, which is the worse failure.
* **Only the holder may release.** The owner token is unique per acquisition, so
  a restarted process that happens to reuse a pid cannot release the lease that
  a different process is currently holding.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import socket
import time
from typing import Optional
from uuid import uuid4

from app.services import db

EXIT = "exit"
PROTECTION = "protection"

#: Long enough to cover the slowest exit preparation we actually make — a GTT
#: cancel, a trigger-status probe, a fresh holdings read and the order itself —
#: so a lease only ever looks stale when its holder really is gone.
DEFAULT_TTL_S = 120.0


@dataclass(frozen=True)
class Lease:
    scope: str
    account_id: str
    uid: str
    symbol: str
    owner: str
    expires_ms: int


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"


@contextmanager
def _transaction():
    if not db.is_available():
        raise RuntimeError("execution_lease_unavailable")
    with db._conn() as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def acquire(scope: str, *, account_id: str, uid: str, symbol: str,
            ttl_s: float = DEFAULT_TTL_S) -> Optional[str]:
    """Take the lease, or None when a live holder already has it.

    Returns the owner token, which ``release`` requires.
    """
    if scope not in (EXIT, PROTECTION):
        raise ValueError("invalid_lease_scope")
    if not account_id or not uid or not symbol:
        raise ValueError("invalid_lease_key")
    now = int(time.time() * 1000)
    expires = now + int(ttl_s * 1000)
    owner = _owner()
    with _transaction() as conn:
        # Steal only an expired row. A bare ON CONFLICT UPDATE overwrote a live
        # holder when two processes both saw an empty SELECT.
        cur = conn.execute(
            """INSERT INTO kite_execution_leases
                 (scope,account_id,uid,symbol,owner,acquired_ms,expires_ms)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(scope,account_id,uid,symbol) DO UPDATE SET
                 owner=excluded.owner, acquired_ms=excluded.acquired_ms,
                 expires_ms=excluded.expires_ms
               WHERE kite_execution_leases.expires_ms <= excluded.acquired_ms""",
            (scope, account_id, uid, symbol, owner, now, expires))
        if cur.rowcount != 1:
            return None
        held = conn.execute(
            """SELECT owner FROM kite_execution_leases
                WHERE scope=? AND account_id=? AND uid=? AND symbol=?""",
            (scope, account_id, uid, symbol)).fetchone()
        if held is None or str(held["owner"]) != owner:
            return None
        return owner


def release(scope: str, *, account_id: str, uid: str, symbol: str, owner: str) -> bool:
    """Give the lease up. False means someone else holds it now."""
    if not owner:
        return False
    with _transaction() as conn:
        return conn.execute(
            """DELETE FROM kite_execution_leases
                WHERE scope=? AND account_id=? AND uid=? AND symbol=? AND owner=?""",
            (scope, account_id, uid, symbol, owner)).rowcount == 1


def holder(scope: str, *, account_id: str, uid: str, symbol: str) -> Optional[Lease]:
    """The current live holder, or None when the lease is free or expired."""
    if not db.is_available():
        return None
    now = int(time.time() * 1000)
    with db._conn() as conn:
        row = conn.execute(
            """SELECT * FROM kite_execution_leases
                WHERE scope=? AND account_id=? AND uid=? AND symbol=?""",
            (scope, account_id, uid, symbol)).fetchone()
    if row is None or int(row["expires_ms"]) <= now:
        return None
    return Lease(scope=row["scope"], account_id=row["account_id"], uid=row["uid"],
                 symbol=row["symbol"], owner=row["owner"],
                 expires_ms=int(row["expires_ms"]))


@contextmanager
def guard(scope: str, *, account_id: str, uid: str, symbol: str,
          ttl_s: float = DEFAULT_TTL_S):
    """Hold a lease for the duration of a block; yields None when refused.

    Callers must check for None rather than assuming the block owns the position:
    a refused lease means another process is mid-conversation with the broker.
    """
    token = acquire(scope, account_id=account_id, uid=uid, symbol=symbol, ttl_s=ttl_s)
    try:
        yield token
    finally:
        if token:
            release(scope, account_id=account_id, uid=uid, symbol=symbol, owner=token)


def clear_for_tests(uid: str = "") -> None:
    if not db.is_available():
        return
    with _transaction() as conn:
        if uid:
            conn.execute("DELETE FROM kite_execution_leases WHERE uid=?", (uid,))
        else:
            conn.execute("DELETE FROM kite_execution_leases")
