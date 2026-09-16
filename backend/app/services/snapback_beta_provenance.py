"""How each hedge beta was arrived at.

Beta sizes the hedge, so it moves P&L directly. A bare float cannot show which
sessions it used, whether it was clamped, or that nothing after the signal leaked in.
The arithmetic here is the SAME alignment and covariance the engine's `rolling_beta()`
performs — this module reads its output and records the window, never re-deriving the
economics.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)

INCONCLUSIVE = "INCONCLUSIVE_CAUSAL_BETA"
OK = "OK"

METHOD_ROLLING = "ROLLING_BETA_60"
METHOD_CANONICAL = "CANONICAL_INDEX_BETA"


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class BetaSnapshot:
    symbol: str
    market_symbol: str
    method: str
    window_sessions: int
    signal_session: str
    beta_effective_session: Optional[str] = None
    window_start_session: Optional[str] = None
    window_end_session: Optional[str] = None
    aligned_observation_count: int = 0
    raw_beta: Optional[float] = None
    clamped_beta: Optional[float] = None
    clamp_low: float = 0.0
    clamp_high: float = 0.0
    was_clamped: bool = False
    underlying_series_hash: Optional[str] = None
    market_series_hash: Optional[str] = None
    aligned_returns_hash: Optional[str] = None
    status: str = OK
    reason_codes: tuple = ()

    def as_row(self, *, opportunity_id: str) -> Dict[str, Any]:
        from app.engines.snapback.manifest import compute_config_hash

        payload = {
            "opportunity_id": opportunity_id,
            "symbol": self.symbol,
            "market_symbol": self.market_symbol,
            "method": self.method,
            "status": self.status,
            "window_sessions": int(self.window_sessions),
            "signal_session": self.signal_session,
            "beta_effective_session": self.beta_effective_session,
            "window_start_session": self.window_start_session,
            "window_end_session": self.window_end_session,
            "aligned_observation_count": int(self.aligned_observation_count),
            "raw_beta": self.raw_beta,
            "clamped_beta": self.clamped_beta,
            "clamp_low": float(self.clamp_low),
            "clamp_high": float(self.clamp_high),
            "was_clamped": 1 if self.was_clamped else 0,
            "underlying_series_hash": self.underlying_series_hash,
            "market_series_hash": self.market_series_hash,
            "aligned_returns_hash": self.aligned_returns_hash,
            "reason_codes_json": json.dumps(list(self.reason_codes)),
            "config_hash": compute_config_hash(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        payload["beta_snapshot_id"] = (
            f"BETA-{opportunity_id}-{self.symbol}-{self.signal_session}"
        )
        payload["payload_hash"] = _sha(
            {k: v for k, v in payload.items()
             if k not in ("beta_snapshot_id", "payload_hash", "observed_at")}
        )
        return payload


def canonical_index_beta(*, symbol: str, signal_session: str) -> BetaSnapshot:
    """The market's beta against itself is 1.0 by definition, not by regression."""
    from app.engines.snapback.hedge import BETA_BOUNDS

    return BetaSnapshot(
        symbol=symbol, market_symbol=symbol, method=METHOD_CANONICAL,
        window_sessions=0, signal_session=signal_session,
        beta_effective_session=signal_session, aligned_observation_count=0,
        raw_beta=1.0, clamped_beta=1.0,
        clamp_low=float(BETA_BOUNDS[0]), clamp_high=float(BETA_BOUNDS[1]),
        was_clamped=False, status=OK,
    )


def _series_rows(bars) -> List[List[Any]]:
    return [[bars.day(i), float(bars.close[i])] for i in range(len(bars))]


def causal_beta_with_provenance(
    stock_bars,
    market_bars,
    *,
    signal_session: str,
    market_symbol: str = "NIFTY",
    symbol: str = "",
) -> BetaSnapshot:
    """Beta as of `signal_session`, with the window that produced it.

    The value comes from the engine's `rolling_beta()`; this function records which
    sessions fed it. Nothing after the signal session is ever used.
    """
    from app.engines.snapback.hedge import BETA_BOUNDS, BETA_WINDOW, rolling_beta

    lo, hi = float(BETA_BOUNDS[0]), float(BETA_BOUNDS[1])
    reasons: List[str] = []

    if stock_bars is None or market_bars is None:
        return BetaSnapshot(
            symbol=symbol, market_symbol=market_symbol, method=METHOD_ROLLING,
            window_sessions=BETA_WINDOW, signal_session=signal_session,
            clamp_low=lo, clamp_high=hi, status=INCONCLUSIVE,
            reason_codes=("missing_market_tape",),
        )

    betas = rolling_beta(stock_bars, market_bars)
    if not betas:
        return BetaSnapshot(
            symbol=symbol, market_symbol=market_symbol, method=METHOD_ROLLING,
            window_sessions=BETA_WINDOW, signal_session=signal_session,
            clamp_low=lo, clamp_high=hi, status=INCONCLUSIVE,
            reason_codes=("insufficient_aligned_observations",),
        )

    # Beta as it stood at the signal: that session, else the most recent before it.
    effective = signal_session if signal_session in betas else None
    if effective is None:
        prior = [day for day in betas if day <= signal_session]
        effective = max(prior) if prior else None

    if effective is None:
        return BetaSnapshot(
            symbol=symbol, market_symbol=market_symbol, method=METHOD_ROLLING,
            window_sessions=BETA_WINDOW, signal_session=signal_session,
            clamp_low=lo, clamp_high=hi, status=INCONCLUSIVE,
            reason_codes=("insufficient_history_before_signal",),
        )

    value = float(betas[effective])

    # The aligned window that produced it, for reconstruction.
    market_at = {market_bars.day(i): float(market_bars.close[i])
                 for i in range(len(market_bars))}
    common = [(stock_bars.day(i), float(stock_bars.close[i]), market_at[stock_bars.day(i)])
              for i in range(len(stock_bars)) if stock_bars.day(i) in market_at]

    index = next((i for i, row in enumerate(common) if row[0] == effective), None)
    window_start = window_end = None
    aligned_pairs: List[List[Any]] = []
    if index is not None and index >= BETA_WINDOW:
        window = common[index - BETA_WINDOW:index]
        window_start, window_end = window[0][0], window[-1][0]
        aligned_pairs = [[row[0], row[1], row[2]] for row in window]

    raw = value
    was_clamped = value in (lo, hi)

    return BetaSnapshot(
        symbol=symbol or getattr(stock_bars, "symbol", ""),
        market_symbol=market_symbol,
        method=METHOD_ROLLING,
        window_sessions=BETA_WINDOW,
        signal_session=signal_session,
        beta_effective_session=effective,
        window_start_session=window_start,
        window_end_session=window_end,
        aligned_observation_count=len(aligned_pairs),
        raw_beta=raw,
        clamped_beta=value,
        clamp_low=lo, clamp_high=hi, was_clamped=was_clamped,
        underlying_series_hash=_sha(_series_rows(stock_bars)),
        market_series_hash=_sha(_series_rows(market_bars)),
        aligned_returns_hash=_sha(aligned_pairs) if aligned_pairs else None,
        status=OK,
        reason_codes=tuple(reasons),
    )


def record_beta_snapshot(warehouse, *, opportunity_id: str, snapshot: BetaSnapshot) -> None:
    """Persist the provenance, including when the calculation failed."""
    warehouse.record_beta_snapshot_row(**snapshot.as_row(opportunity_id=opportunity_id))
