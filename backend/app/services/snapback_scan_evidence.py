"""One durable artifact per scanned symbol.

"200 scanned, 0 signals" is an assertion until each of those symbols leaves a record
of the exact inputs it was judged on. These rows are built from the engine's OWN
`features()` / `fires()` outputs — no second implementation of the rules exists here,
because two implementations would eventually disagree and nobody would know which was
the strategy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

NO_SIGNAL = "NO_FROZEN_SIGNAL"
SIGNAL_FADE_UP = "SIGNAL_FADE_UP"
SIGNAL_FADE_DOWN = "SIGNAL_FADE_DOWN"
MARKET_GATE_REJECTED = "MARKET_GATE_REJECTED"
RV_UNAVAILABLE = "RV_UNAVAILABLE"
BAR_WINDOW_INCOMPLETE = "BAR_WINDOW_INCOMPLETE"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def input_window_hash(bars) -> str:
    """Hash the exact completed bars consumed, so the inputs are reconstructible."""
    rows = [
        [
            int(bars.time[i]) if hasattr(bars, "time") else bars.day(i),
            float(bars.open[i]), float(bars.high[i]), float(bars.low[i]),
            float(bars.close[i]), float(bars.volume[i]),
        ]
        for i in range(len(bars))
    ]
    return _sha(rows)


def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    import math

    return out if math.isfinite(out) else None


def build_symbol_decision(
    *,
    session_date: str,
    symbol: str,
    bars,
    cfg,
    market_gate_status: str,
    market_gate_passed: bool,
    identity: Optional[Any] = None,
    signals: Optional[List[Any]] = None,
    experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the artifact from the engine's own feature and rule outputs."""
    from app.engines.snapback.manifest import compute_config_hash, compute_rule_hash
    from app.engines.snapback.policy import EXECUTION_POLICY
    from app.engines.snapback.strategy import features, fires

    engine_features = features(bars, cfg)
    last = len(bars) - 1

    codes: List[str] = []
    fade_up = fade_down = False

    if last < 0:
        codes.append(BAR_WINDOW_INCOMPLETE)
    elif last < cfg.warmup_bars():
        codes.append(BAR_WINDOW_INCOMPLETE)
    else:
        rv = _f(engine_features.rv[last])
        if rv is None or rv <= 0:
            codes.append(RV_UNAVAILABLE)
        # The same rule evaluation the strategy uses, never a re-stated threshold.
        fade_up = bool(fires(engine_features, last, "fade_up", cfg, bars))
        fade_down = bool(fires(engine_features, last, "fade_down", cfg, bars))

    if not market_gate_passed:
        codes.append(MARKET_GATE_REJECTED)
    if fade_up:
        codes.append(SIGNAL_FADE_UP)
    if fade_down:
        codes.append(SIGNAL_FADE_DOWN)

    emitted = list(signals or [])
    if not emitted and not (fade_up or fade_down):
        codes.append(NO_SIGNAL)

    ident = identity.as_row() if identity is not None else {}
    bar_ms = int(bars.time[last]) if last >= 0 and hasattr(bars, "time") else 0

    payload = {
        "session_date": session_date,
        "experiment_id": experiment_id or os.environ.get(
            "STERLING_EXPERIMENT_ID", "prospective_runtime_1_1"
        ),
        "canonical_symbol": symbol,
        "cash_exchange": ident.get("cash_exchange", ""),
        "cash_tradingsymbol": ident.get("cash_tradingsymbol", ""),
        "cash_instrument_token": int(ident.get("cash_instrument_token", 0) or 0),
        "config_hash": compute_config_hash(cfg),
        "rule_hash": compute_rule_hash(cfg),
        "execution_policy_hash": EXECUTION_POLICY.policy_hash(),
        "bar_timestamp_ms": bar_ms,
        "input_window_hash": input_window_hash(bars),
        "bars_used": len(bars),
        "close": _f(bars.close[last]) if last >= 0 else None,
        "ema": _f(engine_features.ema[last]) if last >= 0 else None,
        "atr": _f(engine_features.atr[last]) if last >= 0 else None,
        "stretch_atr": _f(engine_features.stretch[last]) if last >= 0 else None,
        "prior_high": _f(bars.high[last]) if last >= 0 else None,
        "prior_low": _f(bars.low[last]) if last >= 0 else None,
        "realized_vol": _f(engine_features.rv[last]) if last >= 0 else None,
        "rv_percentile": None,
        "market_gate_status": market_gate_status,
        "market_gate_passed": 1 if market_gate_passed else 0,
        "fade_up_fired": 1 if fade_up else 0,
        "fade_down_fired": 1 if fade_down else 0,
        "signal_emitted": 1 if emitted else 0,
        "emitted_signal_id": str(getattr(emitted[0], "signal_id", "")) if emitted else "",
        "decision_codes_json": json.dumps(codes),
    }
    payload["decision_id"] = f"SCAN-{session_date}-{symbol}"
    payload["payload_hash"] = _sha({k: v for k, v in payload.items() if k != "decision_id"})
    return payload


def record_symbol_decision(warehouse, decision: Dict[str, Any]) -> None:
    """Append one symbol decision. Contradicting an existing one is an integrity error."""
    warehouse.record_scan_symbol_decision(**decision)


def decisions_reconcile(warehouse, *, session_date: str, expected: int) -> bool:
    """Does the number of durable decisions match the universe the scanner claims?"""
    try:
        rows = warehouse.get_records_by_table("scan_symbol_decisions")
    except Exception as exc:
        log.warning("Scan evidence: decisions unreadable: %s", exc)
        return False
    observed = sum(1 for r in rows if str(dict(r).get("session_date")) == session_date)
    return observed >= int(expected or 0) and observed > 0
