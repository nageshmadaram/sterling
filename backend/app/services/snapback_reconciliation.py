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


@dataclass(frozen=True)
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

    if warehouse is None:
        from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

        warehouse = SnapbackObservationWarehouse()

    try:
        positions = [dict(p) for p in (warehouse.get_active_paper_positions() or [])]
    except Exception as exc:
        log.warning("Reconciliation: Sterling positions unreadable: %s", exc)
        positions = []

    client = None
    try:
        client = await acquire_family_client()
    except Exception as exc:
        log.warning("Reconciliation: family client unavailable: %s", exc)

    return _remember(await reconcile_account(
        client=client, account_id=account_id,
        sterling_positions=positions, sterling_intents=[],
    ))


_LATEST: Optional[ReconciliationSnapshot] = None


def latest_reconciliation() -> Optional[ReconciliationSnapshot]:
    """The most recent snapshot this process produced, or None if never run."""
    return _LATEST


def _remember(snapshot: ReconciliationSnapshot) -> ReconciliationSnapshot:
    global _LATEST
    _LATEST = snapshot
    return snapshot
