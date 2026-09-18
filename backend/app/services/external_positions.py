"""Positions the broker holds that Sterling did not open.

Sterling's own stores said the account was flat: no intent, no fill, no registry
entry. The broker said otherwise — 16625 of a CDSL call, bought overnight. Both
were telling the truth about different things, and the dangerous reading is the
local one, because "nothing in my journal" is not "the account is flat".

So a broker position with no matching Sterling record is recorded as what it is:
EXTERNAL_UNMANAGED. Not imported, not adopted, not given an invented intent or
fill. Importing it as though Sterling had opened it would corrupt the provenance
of every number downstream — the promotion gate would count a trade nobody's
strategy made, with an entry price nobody's rule chose.

An external position is exposure. It blocks the flatness gate, it appears in the
digest, and it prevents a start sequence from declaring the machine CLEAN. What
it does NOT do is become Sterling's to manage: no protection is inferred, no
stop is armed, nothing is sold. Whether to hold or exit it is a trading
decision, and this module deliberately cannot make one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "ExternalPosition", "ExternalExposure", "classify_broker_positions",
    "external_exposure", "render_external", "SOURCE", "DISCOVERY_REASON",
]

SOURCE: str = "BROKER_EXTERNAL"
DISCOVERY_REASON: str = "RECONCILIATION_UNKNOWN_POSITION"


@dataclass(frozen=True)
class ExternalPosition:
    """A live broker position with no Sterling intent behind it."""

    account: str
    instrument: str
    quantity: int
    product: str = ""
    exchange: str = ""
    broker_avg_price: float | None = None
    last_price: float | None = None
    unrealised: float | None = None
    observed_at: str = ""

    #: Fixed provenance. These are not booleans a later caller may flip: they
    #: describe how the row entered the system, and it never entered any other way.
    source: str = SOURCE
    managed_by_sterling: bool = False
    discovery_reason: str = DISCOVERY_REASON
    protection_known: bool = False
    sterling_intent: str = "none"
    sterling_fill: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "managed_by_sterling": self.managed_by_sterling,
            "account": self.account,
            "instrument": self.instrument,
            "quantity": self.quantity,
            "product": self.product,
            "exchange": self.exchange,
            "broker_avg_price": self.broker_avg_price,
            "last_price": self.last_price,
            "unrealised": self.unrealised,
            "discovery_reason": self.discovery_reason,
            "protection_known": self.protection_known,
            "sterling_intent": self.sterling_intent,
            "sterling_fill": self.sterling_fill,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class ExternalExposure:
    """What the broker holds that Sterling did not open. None means unreadable."""

    positions: tuple[ExternalPosition, ...] = ()
    #: None when the broker could not be asked. Never an empty list in that case.
    readable: bool = True
    detail: str = ""

    @property
    def count(self) -> int | None:
        return len(self.positions) if self.readable else None

    @property
    def flat(self) -> bool | None:
        """True only when the broker was asked and holds nothing unmanaged."""
        if not self.readable:
            return None
        return not self.positions

    def as_dict(self) -> dict[str, Any]:
        return {"readable": self.readable, "count": self.count, "flat": self.flat,
                "detail": self.detail,
                "positions": [p.as_dict() for p in self.positions]}


def _known_symbols() -> set[str] | None:
    """Every instrument Sterling believes it has a position or intent in.

    ``None`` when the local stores cannot be read: an unreadable local side
    means every broker position is of unknown origin, which must not quietly
    become "all of them are external" or "none of them are".
    """
    try:
        from app.services import db

        db.init()
        from app.services.kite_engine import order_journal, positions

        known: set[str] = set()
        for uid in positions.known_uids():
            known.update(p.symbol for p in positions.open_positions(uid))
            known.update(i.symbol for i in order_journal.unresolved(uid))
        return known
    except Exception as exc:  # noqa: BLE001
        log.warning("external positions: local state unreadable: %s", exc)
        return None


def classify_broker_positions(
    rows: Iterable[Mapping[str, Any]],
    *,
    account: str,
    known_symbols: Sequence[str] | set[str] | None,
    observed_at: str | None = None,
) -> ExternalExposure:
    """Split broker positions into ones Sterling knows and ones it does not."""
    if known_symbols is None:
        return ExternalExposure(
            readable=False,
            detail="Sterling's own position and intent stores could not be read, so "
                   "no broker position can be attributed or excluded")

    stamp = observed_at or datetime.now(timezone.utc).isoformat()
    known = {str(s).upper() for s in known_symbols}
    external: list[ExternalPosition] = []

    for row in rows:
        quantity = int(row.get("quantity") or 0)
        if quantity == 0:
            # A closed position is not exposure. It stays out of the count and
            # out of the report.
            continue
        symbol = str(row.get("tradingsymbol") or row.get("symbol") or "").upper()
        if symbol in known:
            continue
        external.append(ExternalPosition(
            account=account,
            instrument=symbol,
            quantity=quantity,
            product=str(row.get("product") or ""),
            exchange=str(row.get("exchange") or ""),
            broker_avg_price=_float(row.get("average_price")),
            last_price=_float(row.get("last_price")),
            unrealised=_float(row.get("unrealised") if row.get("unrealised") is not None
                              else row.get("pnl")),
            observed_at=stamp,
        ))

    return ExternalExposure(positions=tuple(external))


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def external_exposure(client: Any = None, *, account: str = "") -> ExternalExposure:
    """Ask the broker what it holds, and classify it. Never raises.

    A broker that cannot be asked is UNREADABLE, not flat. That distinction is
    the whole reason this function exists.
    """
    resolved_client = client
    resolved_account = account

    if resolved_client is None:
        try:
            from app.services.exchanges.kite import accounts as kite_accounts

            kite_accounts.bootstrap()
            live = [a for a in kite_accounts.all_accounts() if not getattr(a, "is_paper", True)]
            if not live:
                return ExternalExposure(
                    readable=False, detail="no live broker account is configured")
            resolved_account = resolved_account or str(
                getattr(live[0], "kite_user_id", "") or live[0].id)
            resolved_client = await kite_accounts.acquire_client(live[0])
        except Exception as exc:  # noqa: BLE001
            return ExternalExposure(readable=False,
                                    detail=f"broker client unavailable: {exc}")

    try:
        payload = await resolved_client.get_positions_raw()
        rows = list((payload or {}).get("net") or [])
    except Exception as exc:  # noqa: BLE001
        # The type matters: a bare TimeoutError stringifies to nothing, and a
        # detail of "could not be read: " tells an operator exactly zero.
        return ExternalExposure(
            readable=False,
            detail=f"broker positions could not be read: {type(exc).__name__}: {exc}".rstrip(": "))

    return classify_broker_positions(
        rows, account=resolved_account or "unknown", known_symbols=_known_symbols())


def render_external(exposure: ExternalExposure) -> str:
    if not exposure.readable:
        return f"EXTERNAL BROKER EXPOSURE: UNKNOWN — {exposure.detail}"
    if not exposure.positions:
        return "EXTERNAL BROKER EXPOSURE: none (the broker holds nothing Sterling did not open)"

    lines = [f"EXTERNAL BROKER EXPOSURE: {len(exposure.positions)} position(s) "
             "Sterling did not open and does not manage", ""]
    for position in exposure.positions:
        lines.append(
            f"  {position.instrument}  qty {position.quantity}  {position.product}"
            f"  avg {position.broker_avg_price}  last {position.last_price}"
        )
        lines.append(f"      account {position.account} | {position.source}"
                     f" | {position.discovery_reason}")
        lines.append("      Sterling has no intent, no fill and no protection for this "
                     "position. It is not managed here.")
    return "\n".join(lines)
