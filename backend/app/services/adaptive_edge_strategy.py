"""The bridge between the running engine and the canonical strategy pipeline.

`strategy_pipeline.run_strategy_semantics_pipeline` composes the whole A-to-K
sequence the Master Specification describes. Until this module existed nothing
in `app/services` or `app/api` imported it, so the mathematics was implemented
and the engine that ran never called it.

Two things are deliberately kept apart here.

**The pipeline decides direction and economics.** That is the specification's
work: causal features, directional hypothesis, adaptive horizon, edge, F-004
viability, risk and sizing.

**The scanner decides the instrument.** `select_option_contract` inside the
pipeline builds a tradingsymbol by string formatting — hardcoded NIFTY prefix,
a 50-point strike step, a guessed expiry code. That is fine as a research label
and unusable as an order: a fabricated key either rejects at the exchange or
resolves to a contract nobody chose. The real listed contracts come from the
instrument dump, already filtered for liquidity and expiry, and this module
never uses the synthesized symbol for anything an order touches.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from app.core.logging import get_logger
from app.engines.adaptive_edge import AdaptiveEdgeConfig
from app.engines.adaptive_edge.kite_events import bar_events, interval_seconds
from app.engines.adaptive_edge.strategy_pipeline import (
    StrategyConfig,
    StrategyExecutionResult,
    run_strategy_semantics_pipeline,
)

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

#: The pipeline needs enough bars to build a value area, an initial balance and a
#: volume profile before any decision means anything. Below this it will still
#: return something, and that something is an artefact of a short window rather
#: than a reading of the market.
MIN_BARS = 40


@dataclass(frozen=True)
class PipelineDecision:
    """What the canonical pipeline concluded for one underlying."""

    underlying: str
    direction: str                   # BULLISH / BEARISH / NEUTRAL
    horizon: str
    reason: str
    uncertainty: float
    target_points: float
    stop_points: float
    expected_net_value: float
    eligible: bool
    bars: int
    trace_hash: str
    #: The pipeline's own synthesized contract. Carried for provenance and
    #: diagnostics only — never used to place an order. See the module docstring.
    reference_instrument: Optional[str] = None

    @property
    def actionable(self) -> bool:
        """Whether this decision may open a position.

        Both terms are the source's, not tuning: §35 requires a direction and a
        positive expected value before any BUY_CE or BUY_PE.
        """
        return self.direction in ("BULLISH", "BEARISH") and self.eligible

    @property
    def option_type(self) -> Optional[str]:
        if self.direction == "BULLISH":
            return "CE"
        if self.direction == "BEARISH":
            return "PE"
        return None


def strategy_config_for(cfg: AdaptiveEdgeConfig, *, symbol: str,
                        expiry: str, spot: float) -> StrategyConfig:
    """Map the engine's configuration onto the pipeline's.

    Only fields that mean the same thing on both sides are carried across.
    `stop_points` is derived from the configured percentage against the current
    spot, because the pipeline works in points and the engine's stop is a
    percentage of premium — translating rather than assuming they are the same
    number.
    """
    stop_points = max(1.0, spot * (cfg.stop_percent / 100.0)) if spot > 0 else cfg.stop_percent
    strat_ver = getattr(cfg, "strategy_version", "v2_hardened") or "v2_hardened"
    return StrategyConfig(
        strategy_version=str(strat_ver),
        symbol=symbol,
        tick_size=0.05,
        execution_cost=max(0.0, cfg.fee_rate * max(spot, 1.0)),
        min_net_value=cfg.min_expected_net_value,
        authorized_risk=max(1.0, cfg.risk_pct * 1000.0),
        max_quantity=max(1, cfg.lots * 100),
        stop_points=stop_points,
        target_rr=cfg.target_multiple,
        option_expiry=expiry,
    )


async def fetch_bars(client, token: int, *, interval: str, lookback_bars: int) -> list[dict]:
    """Historical candles for one instrument, newest window only.

    The window is sized from the interval so a caller asking for 120 bars gets
    roughly 120 bars rather than a fixed number of days that means something
    different at every timeframe.
    """
    if token <= 0:
        return []
    seconds = interval_seconds(interval) * max(lookback_bars, MIN_BARS)
    # Widened for weekends and holidays: a strict window over a long weekend
    # returns almost nothing and the engine reports "no data" for a market that
    # was simply closed.
    now = datetime.now(_IST)
    start = now - timedelta(seconds=int(seconds * 3.0))
    payload = await client.get_historical(
        token, interval,
        start.strftime("%Y-%m-%d %H:%M:%S"),
        now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    # The client returns {"candles": [...]} at the top level; some Kite paths
    # nest it under "data". Read both. Reading only the nested form returned
    # zero every time, so the pipeline never saw a bar and silently made no
    # decision — the scan reported "not enough history" on a full session of it.
    payload = payload or {}
    rows = payload.get("candles") or (payload.get("data") or {}).get("candles") or []
    out: list[dict] = []
    for row in rows:
        # Kite returns positional candles: [timestamp, o, h, l, c, volume, oi?]
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        stamp = row[0]
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_IST)
        out.append({
            "timestamp_ms": int(parsed.timestamp() * 1000),
            "open": row[1], "high": row[2], "low": row[3], "close": row[4],
            "volume": row[5], "oi": row[6] if len(row) > 6 else 0,
        })
    return out[-max(lookback_bars, MIN_BARS):]


def decide_from_candles(underlying: str, candles: list[dict], cfg: AdaptiveEdgeConfig,
                        *, expiry: str, spot: float) -> Optional[PipelineDecision]:
    """Run the canonical pipeline over one underlying's bars.

    Returns None when there is not enough history to decide. That is different
    from NEUTRAL: NEUTRAL is the strategy declining, None is the engine not
    being in a position to ask.
    """
    events = bar_events(underlying, candles, interval=cfg.decision_timeframe)
    if len(events) < MIN_BARS:
        return None

    config = strategy_config_for(cfg, symbol=underlying, expiry=expiry, spot=spot)
    try:
        result: StrategyExecutionResult = run_strategy_semantics_pipeline(
            events, (), config=config)
    except Exception as exc:                                       # noqa: BLE001
        # A pipeline failure must not take the scan down; it is reported as an
        # absence of decision for this underlying rather than as a crash.
        log.exception("adaptive_edge: strategy pipeline failed for %s: %s", underlying, exc)
        return None

    decision = result.market_decision
    return PipelineDecision(
        underlying=underlying,
        direction=str(decision.direction),
        horizon=getattr(decision.horizon, "value", str(decision.horizon)),
        reason=str(decision.decision_reason),
        uncertainty=float(decision.uncertainty),
        target_points=float(decision.target_points),
        stop_points=float(decision.stop_points),
        expected_net_value=float(result.economic_assessment.expected_net_value),
        eligible=bool(result.economic_assessment.eligible),
        bars=len(events),
        trace_hash=str(result.trace_hash),
        reference_instrument=result.selected_instrument,
    )


# --------------------------------------------------------- F-105 economics

# There is no F-102 model loader here on purpose.
#
# One was written, and removed the same day. It would have fed F-102's class
# probabilities into F-105's option-payoff slots — mapping P(UP) to p_target for
# a call — and those are different events: P(UP) is "the underlying moves 8 bps
# within 15 bars", target_price is "the premium doubles". With the measured
# no-edge probabilities and a 2:1 payoff that mapping produced a positive,
# eligible conservative EV out of a model with no directional signal.
#
# A loader with no valid consumer is the dead code this engine has already been
# audited for once. The probabilities F-105 needs have to be measured from
# premium excursions, which is what premium_excursion_probabilities below does
# and what the observation recorder collects.

def conservative_ev(
    *,
    premium: float,
    target_price: float,
    stop_price: float,
    p_target: float,
    p_stop: float,
    execution_cost: float,
    sample_size: int,
    confidence: float = 0.95,
):
    """F-105: the conservative expected value, from premium-excursion probabilities.

    This is the quantity §35 requires. F-105 computes it as
    `net_ev - z * standard_error`, where the standard error is the finite-sample
    spread of the three-outcome payoff — a genuine lower confidence bound.

    **`p_target` and `p_stop` must be probabilities that the OPTION PREMIUM
    reaches those levels.** They are not directional probabilities, and the
    signature takes them separately for that reason.

    An earlier version of this took F-102's class probabilities and mapped
    P(UP) -> p_target for a call. That is a category error and it is not a
    subtle one. P(UP) is "the underlying moves more than 8 bps within 15 bars";
    `target_price` is "the premium doubles". An at-the-money option needs a far
    larger underlying move than 8 bps to double, so P(UP) overstates P(target)
    by a large and unknown factor.

    The consequence was not a rounding error. With the measured, no-edge
    probabilities (P(UP) 0.185 against P(DOWN) 0.207 — a losing hit rate) and a
    2:1 payoff, that mapping produced `conservative_ev = +11.61, eligible=True`.
    A model with no directional signal was manufacturing a positive expectancy
    purely from the asymmetry of the payoff slots it was fed into.

    The premium-excursion probabilities have to be measured: how often a
    contract like this one, entered in a state like this one, actually reaches
    its target before its stop. That is what the observation recorder collects,
    and until it has, this returns None rather than a number.
    """
    from app.engines.adaptive_edge.f105_economics import F105Candidate, evaluate_candidate

    p_target = float(p_target)
    p_stop = float(p_stop)
    if p_target < 0 or p_stop < 0 or (p_target + p_stop) > 1.0:
        return None
    p_neither = 1.0 - p_target - p_stop

    try:
        return evaluate_candidate(
            F105Candidate(
                entry_price=float(premium),
                target_price=float(target_price),
                stop_price=float(stop_price),
                p_target=p_target,
                p_stop=p_stop,
                p_neither=p_neither,
            ),
            execution_cost=float(execution_cost),
            sample_size=int(sample_size),
            confidence=confidence,
        )
    except ValueError as exc:
        log.debug("adaptive_edge: F-105 refused a candidate (%s)", exc)
        return None


def premium_excursion_probabilities(observations: Sequence[dict], *,
                                    target_multiple: float,
                                    stop_percent: float) -> Optional[tuple[float, float, int]]:
    """(p_target, p_stop, sample_size) measured from recorded observations.

    The honest source of F-105's probabilities: how often a recorded contract
    actually reached its target before its stop. Returns None until enough
    resolved observations exist, because a bound computed on a handful of rows
    is a number rather than a bound.

    Nothing calls this yet with real data — the recorder has collected no
    resolved sessions. It is here so the path from observation to conservative
    EV is complete and testable rather than described.
    """
    resolved = [o for o in observations or () if o.get("forward_return_pct") is not None]
    if len(resolved) < 200:
        return None
    target_pct = (float(target_multiple) - 1.0) * 100.0
    stop_pct = -abs(float(stop_percent))
    hits = sum(1 for o in resolved if float(o.get("max_favourable_pct") or o["forward_return_pct"]) >= target_pct)
    stops = sum(1 for o in resolved if float(o.get("max_adverse_pct") or o["forward_return_pct"]) <= stop_pct)
    n = len(resolved)
    return hits / n, stops / n, n
