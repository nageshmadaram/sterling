"""Does the broker's book agree with Sterling's?

Sterling does not own the account exclusively: somebody can trade it by hand, an order
can fill while the process was down, a hedge can be half established. Each of those
makes "flat" or "open" a guess — and a guess must block new exposure rather than be
resolved optimistically.

Unknown is never clean.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

CRITICAL = "CRITICAL"
WARNING = "WARNING"

# Every way the two books can disagree.
EXTERNAL_OR_UNKNOWN_POSITION = "EXTERNAL_OR_UNKNOWN_POSITION"
POSITION_MISSING_AT_BROKER = "POSITION_MISSING_AT_BROKER"
QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
HEDGE_MISMATCH = "HEDGE_MISMATCH"
UNKNOWN_ORDER = "UNKNOWN_ORDER"
DUPLICATE_ENTRY_ORDER = "DUPLICATE_ENTRY_ORDER"
PROTECTION_MISSING = "PROTECTION_MISSING"
BROKER_STATE_UNAVAILABLE = "BROKER_STATE_UNAVAILABLE"


@dataclass(frozen=True)
class ReconciliationMismatch:
    code: str
    severity: str
    instrument: Optional[str] = None
    broker_quantity: Optional[int] = None
    sterling_quantity: Optional[int] = None
    order_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class PositionStoreUnreadable(RuntimeError):
    """Sterling's own inventory could not be read.

    Distinct from "Sterling holds nothing": treating one as the other makes
    every real broker position look like an unknown external one.
    """


@dataclass
class ReconciliationSnapshot:
    clean: bool
    account_id: str
    observed_at: str
    broker_positions: Tuple = ()
    broker_orders: Tuple = ()
    broker_trades: Tuple = ()
    sterling_positions: Tuple = ()
    sterling_intents: Tuple = ()
    mismatches: Tuple[ReconciliationMismatch, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "clean": self.clean,
            "account_id": self.account_id,
            "observed_at": self.observed_at,
            "broker_position_count": len(self.broker_positions),
            "sterling_position_count": len(self.sterling_positions),
            "mismatches": [
                {
                    "code": m.code, "severity": m.severity, "instrument": m.instrument,
                    "broker_quantity": m.broker_quantity,
                    "sterling_quantity": m.sterling_quantity,
                    "order_id": m.order_id, "details": m.details,
                }
                for m in self.mismatches
            ],
        }


def _get(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


async def reconcile_account(
    *,
    client,
    account_id: str,
    sterling_positions: Sequence[Any],
    sterling_intents: Sequence[Any],
    protection_for: Optional[Any] = None,
) -> ReconciliationSnapshot:
    """Compare the broker's positions, orders and trades with Sterling's own view."""
    observed_at = datetime.now(timezone.utc).isoformat()
    mismatches: List[ReconciliationMismatch] = []

    if client is None:
        return ReconciliationSnapshot(
            clean=False, account_id=account_id, observed_at=observed_at,
            sterling_positions=tuple(sterling_positions or ()),
            sterling_intents=tuple(sterling_intents or ()),
            mismatches=(ReconciliationMismatch(
                code=BROKER_STATE_UNAVAILABLE, severity=CRITICAL,
                details={"reason": "no broker client"},
            ),),
        )

    broker_positions: List[Any] = []
    broker_orders: List[Any] = []
    broker_trades: List[Any] = []
    unavailable = False

    try:
        raw = await client.get_positions_raw()
        broker_positions = list((raw or {}).get("net") or [])
    except Exception as exc:
        unavailable = True
        mismatches.append(ReconciliationMismatch(
            code=BROKER_STATE_UNAVAILABLE, severity=CRITICAL,
            details={"positions_error": str(exc)},
        ))

    for name, getter, sink in (
        ("orders", getattr(client, "get_orders", None), broker_orders),
        ("trades", getattr(client, "get_trades", None), broker_trades),
    ):
        if getter is None:
            continue
        try:
            sink.extend(list(await getter() or []))
        except Exception as exc:
            unavailable = True
            mismatches.append(ReconciliationMismatch(
                code=BROKER_STATE_UNAVAILABLE, severity=CRITICAL,
                details={f"{name}_error": str(exc)},
            ))

    if unavailable:
        return ReconciliationSnapshot(
            clean=False, account_id=account_id, observed_at=observed_at,
            broker_positions=tuple(broker_positions),
            broker_orders=tuple(broker_orders), broker_trades=tuple(broker_trades),
            sterling_positions=tuple(sterling_positions or ()),
            sterling_intents=tuple(sterling_intents or ()),
            mismatches=tuple(mismatches),
        )

    broker_by_symbol: Dict[str, int] = {}
    for row in broker_positions:
        symbol = str(_get(row, "tradingsymbol", "") or "")
        if not symbol:
            continue
        broker_by_symbol[symbol] = broker_by_symbol.get(symbol, 0) + _int(_get(row, "quantity", 0))

    expected: Dict[str, int] = {}
    for row in (sterling_positions or []):
        option_symbol = str(_get(row, "option_symbol", "") or "")
        if option_symbol:
            expected[option_symbol] = expected.get(option_symbol, 0) + _int(_get(row, "option_qty", 0))
        futures_symbol = str(_get(row, "futures_symbol", "") or "")
        lots = _int(_get(row, "current_futures_lots", 0))
        lot_size = _int(_get(row, "futures_lot_size", 0))
        if futures_symbol and lots:
            expected[futures_symbol] = expected.get(futures_symbol, 0) + lots * lot_size

    futures_symbols = {
        str(_get(row, "futures_symbol", "") or "") for row in (sterling_positions or [])
    }

    for symbol, sterling_quantity in expected.items():
        broker_quantity = broker_by_symbol.get(symbol)
        if broker_quantity is None:
            mismatches.append(ReconciliationMismatch(
                code=POSITION_MISSING_AT_BROKER, severity=CRITICAL,
                instrument=symbol, broker_quantity=0,
                sterling_quantity=sterling_quantity,
            ))
        elif broker_quantity != sterling_quantity:
            mismatches.append(ReconciliationMismatch(
                code=HEDGE_MISMATCH if symbol in futures_symbols else QUANTITY_MISMATCH,
                severity=CRITICAL, instrument=symbol,
                broker_quantity=broker_quantity, sterling_quantity=sterling_quantity,
            ))

    for symbol, broker_quantity in broker_by_symbol.items():
        if broker_quantity == 0 or symbol in expected:
            continue
        # Somebody else's trade, or one of ours Sterling has lost track of.
        mismatches.append(ReconciliationMismatch(
            code=EXTERNAL_OR_UNKNOWN_POSITION, severity=CRITICAL,
            instrument=symbol, broker_quantity=broker_quantity, sterling_quantity=0,
        ))

    broker_order_ids = {str(_get(o, "order_id", "") or "") for o in broker_orders}
    for intent in (sterling_intents or []):
        order_id = str(_get(intent, "order_id", "") or "")
        status = str(_get(intent, "status", "") or "").upper()
        if status in {"FILLED", "CANCELLED", "REJECTED", "CLOSED"}:
            continue
        if not order_id or order_id not in broker_order_ids:
            mismatches.append(ReconciliationMismatch(
                code=UNKNOWN_ORDER, severity=CRITICAL,
                order_id=order_id or None,
                details={"intent_id": str(_get(intent, "intent_id", "") or ""),
                         "status": status},
            ))

    entry_orders: Dict[str, List[str]] = {}
    for order in broker_orders:
        if str(_get(order, "status", "") or "").upper() not in {"OPEN", "TRIGGER PENDING", "PENDING"}:
            continue
        if str(_get(order, "transaction_type", "") or "").upper() != "BUY":
            continue
        key = f"{_get(order, 'tag', '')}:{_get(order, 'tradingsymbol', '')}"
        entry_orders.setdefault(key, []).append(str(_get(order, "order_id", "") or ""))

    for key, ids in entry_orders.items():
        if len(ids) > 1:
            mismatches.append(ReconciliationMismatch(
                code=DUPLICATE_ENTRY_ORDER, severity=CRITICAL,
                instrument=key.split(":")[-1], details={"order_ids": ids},
            ))

    # A live option position with no working protection is the single most
    # expensive thing this function can fail to notice. Only an ACTIVE
    # protective order counts: SUBMITTING, MODIFY_PENDING and RECONCILING all
    # mean we do not know the broker is holding one.
    if protection_for is not None:
        for position in broker_positions:
            row = dict(position)
            symbol = str(row.get("tradingsymbol") or row.get("symbol") or "")
            try:
                quantity = int(row.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0
            if not symbol or quantity == 0:
                continue
            try:
                protection = protection_for(symbol)
            except Exception as exc:  # noqa: BLE001 - unknown is unprotected
                protection = None
                log.warning("Reconciliation: protection lookup failed for %s: %s",
                            symbol, exc)
            state = str((protection or {}).get("state") or "")
            if state != "ACTIVE":
                mismatches.append(ReconciliationMismatch(
                    code=PROTECTION_MISSING, severity=CRITICAL,
                    instrument=symbol, broker_quantity=quantity,
                    details={"protection_state": state or "NONE"},
                ))

    return ReconciliationSnapshot(
        clean=not mismatches,
        account_id=account_id,
        observed_at=observed_at,
        broker_positions=tuple(broker_positions),
        broker_orders=tuple(broker_orders),
        broker_trades=tuple(broker_trades),
        sterling_positions=tuple(sterling_positions or ()),
        sterling_intents=tuple(sterling_intents or ()),
        mismatches=tuple(mismatches),
    )


async def reconcile_family_account(warehouse=None) -> ReconciliationSnapshot:
    """Reconcile the bound Family account against Sterling's live view."""
    from app.services.snapback_family_account import acquire_family_client, configured_binding

    try:
        binding = configured_binding()
        account_id = binding.account_id
    except Exception as exc:
        return ReconciliationSnapshot(
            clean=False, account_id="", observed_at=datetime.now(timezone.utc).isoformat(),
            mismatches=(ReconciliationMismatch(
                code=BROKER_STATE_UNAVAILABLE, severity=CRITICAL,
                details={"binding_error": str(exc)},
            ),),
        )

    # The live book, not the paper warehouse. Comparing the broker against
    # prospective paper positions would report a real live position as an
    # unknown external one and a paper position as a missing broker one — the
    # reconciliation would be confidently wrong in both directions.
    uid = _family_uid()
    positions = _canonical_live_positions(uid)
    intents = _unresolved_intents(uid, account_id)

    client = None
    try:
        client = await acquire_family_client()
    except Exception as exc:
        log.warning("Reconciliation: family client unavailable: %s", exc)

    return _remember(await reconcile_account(
        client=client, account_id=account_id,
        sterling_positions=positions, sterling_intents=intents,
        protection_for=lambda symbol: _protection_state(uid, symbol),
    ))


def _protection_state(uid: str, option_symbol: str) -> Optional[Dict[str, Any]]:
    """What the protection registry believes is guarding this symbol."""
    from app.services.kite_engine import protection

    plan = protection.plan_for_symbol(uid, option_symbol)
    if plan is None:
        return None
    return {
        "state": "ACTIVE" if getattr(plan, "gtt_id", None) else "NONE",
        "quantity": int(getattr(plan, "quantity", 0) or 0),
    }


def _family_uid() -> str:
    import os

    return os.environ.get("STERLING_FAMILY_USER_ID", "") or "default"


def _canonical_live_positions(uid: str) -> List[Dict[str, Any]]:
    """Sterling's own live inventory, from the canonical position store.

    An unreadable store is not an empty one: returning [] would assert Sterling
    holds nothing, and every real broker position would then look external.
    """
    from app.services.kite_engine import positions as kite_positions

    reason = ""
    try:
        reason = kite_positions.unreadable_reason(uid) or ""
    except Exception:  # noqa: BLE001
        reason = ""
    if reason:
        raise PositionStoreUnreadable(reason)

    rows: List[Dict[str, Any]] = []
    for p in kite_positions.open_positions(uid) or []:
        rows.append({
            "tradingsymbol": getattr(p, "symbol", ""),
            "symbol": getattr(p, "symbol", ""),
            "quantity": int(getattr(p, "qty", 0) or 0),
            "exchange": getattr(p, "exchange", ""),
            "status": getattr(p, "status", ""),
        })
    return rows


def _unresolved_intents(uid: str, account_id: str) -> List[Dict[str, Any]]:
    """Orders Sterling believes may be working.

    Passing an empty list asserts "nothing is in flight", which is exactly the
    claim that cannot be made without consulting the order journal.
    """
    from app.services.kite_engine import order_journal

    rows: List[Dict[str, Any]] = []
    for intent in order_journal.unresolved(uid, account_id) or []:
        rows.append({
            "intent_key": getattr(intent, "intent_key", ""),
            "order_id": getattr(intent, "order_id", "") or "",
            "state": getattr(intent, "state", ""),
            "tradingsymbol": getattr(intent, "tradingsymbol", ""),
            "quantity": int(getattr(intent, "quantity", 0) or 0),
        })
    return rows


# A snapshot older than roughly one reconciliation cycle describes a book that
# may have moved since. Past this, the honest answer is "unknown", not "clean".
RECONCILIATION_TTL_SECONDS = 60

_LATEST: Optional[ReconciliationSnapshot] = None
_BY_ACCOUNT: Dict[str, ReconciliationSnapshot] = {}


def snapshot_is_fresh(
    snapshot: Optional[ReconciliationSnapshot],
    *,
    now: Optional[datetime] = None,
    ttl_seconds: int = RECONCILIATION_TTL_SECONDS,
) -> bool:
    """Whether this snapshot still describes the present.

    A timestamp without a timezone is not trusted: it could be hours out in
    either direction, and a clean-looking stale snapshot is the worst case.
    """
    if snapshot is None:
        return False
    try:
        observed = datetime.fromisoformat(str(snapshot.observed_at))
    except (TypeError, ValueError):
        return False
    if observed.tzinfo is None:
        return False

    moment = now or datetime.now(timezone.utc)
    return (moment - observed).total_seconds() <= ttl_seconds


def latest_reconciliation(
    account_id: Optional[str] = None,
) -> Optional[ReconciliationSnapshot]:
    """The most recent snapshot, optionally for one exact account.

    Asking about an account this process has never reconciled returns None.
    Reconciling somebody else's book proves nothing about this one, so the
    account-scoped lookup never falls back to the global one.
    """
    if account_id:
        return _BY_ACCOUNT.get(str(account_id))
    return _LATEST


def _remember(snapshot: ReconciliationSnapshot) -> ReconciliationSnapshot:
    global _LATEST
    _LATEST = snapshot
    if snapshot.account_id:
        _BY_ACCOUNT[str(snapshot.account_id)] = snapshot
    return snapshot
