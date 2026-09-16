"""Prospective market evidence: the full FULL-mode tick, kept because it cannot be recovered.

Kite serves no historical candles for expired options and no past ticks at all.
The 2026-09-17 lake inventory showed what that means in practice: zero option
bars behind 746 frozen Snapback signals, and no way to ever obtain them. Every
session that passes without recording is evidence destroyed, not evidence
deferred, so this module errs toward keeping too much.

Two differences from :mod:`kitelake.ticks` decide what questions stay answerable.

**Both clocks.** ``TICK_SCHEMA`` stores one ``ts``, and falls back to local
wall-clock when the exchange timestamp is absent — which makes a fabricated
timestamp indistinguishable from a real one and destroys freshness forever. Here
``exchange_ts`` is nullable and never invented, and ``received_ts`` is recorded
separately. Freshness is ``exchange_ts -> received_ts``; availability at a
decision is ``received_ts <= decision_ts``. Neither can be reconstructed later.

**The whole ladder.** ``TICK_SCHEMA`` keeps ``depth["buy"][0]`` and discards
levels 2-5. Level 1 answers "was there a quote", not "would this quantity have
filled" — so it cannot support execution beyond minimum size. All five levels are
kept with their order counts.

Prices are scaled int64 exactly as bars and ticks are, so the three are directly
comparable and a sizing decision replays byte-for-byte.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pyarrow as pa

from .config import IST
from .schema import encode_price

__all__ = [
    "EVIDENCE_TICK_SCHEMA",
    "DEPTH_LEVELS",
    "SOURCE_KITE_FULL",
    "evidence_tick_row",
    "evidence_tick_path",
    "EVIDENCE_CLASSES",
]

#: Kite FULL mode publishes five levels per side.
DEPTH_LEVELS = 5

SOURCE_KITE_FULL = "KITE_FULL"

#: Mirrors ``app.services.snapback_evidence_class.EVIDENCE_CLASSES``. Duplicated
#: rather than imported because kitelake runs in its own environment and must not
#: depend on the backend package; ``test_evidence_class_parity`` fails if the two
#: ever diverge.
EVIDENCE_CLASSES = ("MODELLED", "OBSERVED_MARKET", "BROKER_SHADOW", "BROKER_EXECUTED")


def _evidence_class(value: object) -> str:
    """Refuse a row whose provenance is missing or unrecognised."""
    if value is None:
        raise ValueError("evidence class is missing; a row without provenance is not evidence")
    name = str(value).strip().upper()
    if name not in EVIDENCE_CLASSES:
        raise ValueError(f"unknown evidence class {value!r}; expected one of {', '.join(EVIDENCE_CLASSES)}")
    return name


def _depth_fields() -> list[pa.Field]:
    fields: list[pa.Field] = []
    for side in ("bid", "ask"):
        for i in range(DEPTH_LEVELS):
            # Null, not zero: an absent level and a level quoting zero are
            # different facts, and only one of them means "no liquidity here".
            fields.append(pa.field(f"{side}{i}_price", pa.int64(), nullable=True))
            fields.append(pa.field(f"{side}{i}_qty", pa.int64(), nullable=True))
            fields.append(pa.field(f"{side}{i}_orders", pa.int64(), nullable=True))
    return fields


EVIDENCE_TICK_SCHEMA = pa.schema(
    [
        # ─── clocks ──────────────────────────────────────────────────────────
        # Nullable and never defaulted: a tick that arrived without an exchange
        # timestamp must stay visibly undated rather than borrow ours.
        pa.field("exchange_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("received_ts", pa.timestamp("us", tz="UTC"), nullable=False),
        # ─── contract identity ───────────────────────────────────────────────
        pa.field("instrument_token", pa.int64(), nullable=False),
        pa.field("tradingsymbol", pa.string(), nullable=True),
        pa.field("exchange", pa.string(), nullable=True),
        pa.field("segment", pa.string(), nullable=True),
        pa.field("instrument_type", pa.string(), nullable=True),
        pa.field("expiry", pa.string(), nullable=True),
        pa.field("strike", pa.int64(), nullable=True),
        pa.field("lot_size", pa.int64(), nullable=True),
        # ─── trade state ─────────────────────────────────────────────────────
        pa.field("last_price", pa.int64(), nullable=True),
        pa.field("last_traded_quantity", pa.int64(), nullable=True),
        pa.field("volume_traded", pa.int64(), nullable=True),
        pa.field("oi", pa.int64(), nullable=True),
        *_depth_fields(),
        # ─── what the underlying was doing ───────────────────────────────────
        # Recorded beside the option, because a delta or a moneyness computed
        # against an unrecorded spot cannot be audited afterwards.
        pa.field("spot_observed", pa.int64(), nullable=True),
        pa.field("spot_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        # ─── why this contract is in the recording ───────────────────────────
        pa.field("opportunity_id", pa.string(), nullable=True),
        pa.field("capture_reason", pa.string(), nullable=True),
        # ─── which code was listening ────────────────────────────────────────
        pa.field("runtime_build_sha", pa.string(), nullable=True),
        pa.field("strategy_config_hash", pa.string(), nullable=True),
        pa.field("strategy_rule_hash", pa.string(), nullable=True),
        pa.field("source", pa.string(), nullable=False),
        pa.field("evidence_class", pa.string(), nullable=False),
    ]
)


def _utc(value: Any) -> Optional[datetime]:
    """Normalise a Kite datetime to UTC, or None. Never substitutes now()."""
    if not isinstance(value, datetime):
        return None
    # kiteconnect hands back naive IST datetimes.
    return (value if value.tzinfo else value.replace(tzinfo=IST)).astimezone(timezone.utc)


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return encode_price(value)
    except (TypeError, ValueError):
        return None


def evidence_tick_row(
    tick: dict[str, Any],
    *,
    received_ts: datetime,
    evidence_class: str,
    contract: Optional[dict[str, Any]] = None,
    spot_observed: Any = None,
    spot_ts: Any = None,
    opportunity_id: Optional[str] = None,
    capture_reason: Optional[str] = None,
    runtime_build_sha: Optional[str] = None,
    strategy_config_hash: Optional[str] = None,
    strategy_rule_hash: Optional[str] = None,
    source: str = SOURCE_KITE_FULL,
) -> dict[str, Any]:
    """Turn one raw FULL tick into an evidence row.

    ``received_ts`` is supplied by the caller rather than taken here, so the
    recorded arrival time is when the socket handed the tick over, not when this
    function happened to run.
    """
    meta = contract or {}
    depth = tick.get("depth") or {}

    row: dict[str, Any] = {
        "exchange_ts": _utc(tick.get("exchange_timestamp")),
        "received_ts": received_ts.astimezone(timezone.utc),
        "instrument_token": int(tick.get("instrument_token") or 0),
        "tradingsymbol": meta.get("tradingsymbol"),
        "exchange": meta.get("exchange"),
        "segment": meta.get("segment"),
        "instrument_type": meta.get("instrument_type"),
        "expiry": str(meta["expiry"]) if meta.get("expiry") else None,
        "strike": _price(meta.get("strike")),
        "lot_size": _int(meta.get("lot_size")),
        "last_price": _price(tick.get("last_price")),
        "last_traded_quantity": _int(tick.get("last_traded_quantity")),
        "volume_traded": _int(tick.get("volume_traded", tick.get("volume"))),
        "oi": _int(tick.get("oi")),
        "spot_observed": _price(spot_observed),
        "spot_ts": _utc(spot_ts),
        "opportunity_id": opportunity_id,
        "capture_reason": capture_reason,
        "runtime_build_sha": runtime_build_sha,
        "strategy_config_hash": strategy_config_hash,
        "strategy_rule_hash": strategy_rule_hash,
        "source": source,
        # Refuses an unknown or missing class outright: a row nobody can classify
        # must not reach the lake, where its provenance would be unrecoverable.
        "evidence_class": _evidence_class(evidence_class),
    }

    for side, key in (("bid", "buy"), ("ask", "sell")):
        levels = depth.get(key) or []
        for i in range(DEPTH_LEVELS):
            level = levels[i] if i < len(levels) else {}
            level = level if isinstance(level, dict) else {}
            row[f"{side}{i}_price"] = _price(level.get("price"))
            row[f"{side}{i}_qty"] = _int(level.get("quantity"))
            row[f"{side}{i}_orders"] = _int(level.get("orders"))

    return row


def evidence_tick_path(day: date, *, root: Any = None) -> Path:
    """One file per day, beside the raw ticks, separate from them.

    Kept apart from ``ticks/<date>/<exchange>.parquet`` on purpose: this stream is
    the audit trail for a capital decision and must not inherit the raw stream's
    retention or compaction.
    """
    from .volume import ticks_dir

    path = ticks_dir(root) / f"date={day.isoformat()}" / "evidence.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
