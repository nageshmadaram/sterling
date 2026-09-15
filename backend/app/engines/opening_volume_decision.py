"""Transparent decision model for Opening Volume Leaders.

ORION's private score weights and Momentum Box predicates are not published.
This module therefore defines a versioned Sterling model whose inputs, weights,
unknown-data treatment, and execution gate are all inspectable.  Unknown
evidence earns no points and remains visible in the score upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from app.engines.opening_volume_leaders import (
    CandleQuality,
    ChaseState,
    LeaderDirection,
    LeaderSignal,
    LeaderTier,
    LiquidityState,
    ValidationState,
)

MODEL_ID = "sterling_opening_decision_v1"


@dataclass(frozen=True)
class OpeningDecisionConfig:
    trade_score: float = 55.0
    special_score: float = 75.0
    conviction_required: int = 5
    repeat_volume_ratio: float = 0.50
    bullish_rsi_min: float = 55.0
    bullish_rsi_max: float = 75.0
    bearish_rsi_min: float = 25.0
    bearish_rsi_max: float = 45.0
    sector_breadth_ratio: float = 1.50

    def validate(self) -> OpeningDecisionConfig:
        numeric = (
            self.trade_score,
            self.special_score,
            self.repeat_volume_ratio,
            self.bullish_rsi_min,
            self.bullish_rsi_max,
            self.bearish_rsi_min,
            self.bearish_rsi_max,
            self.sector_breadth_ratio,
        )
        if not all(isfinite(value) for value in numeric):
            raise ValueError("decision thresholds must be finite")
        if not 0 <= self.trade_score <= self.special_score <= 100:
            raise ValueError("score thresholds must satisfy 0 <= trade <= special <= 100")
        if not 1 <= self.conviction_required <= 7:
            raise ValueError("conviction_required must be between 1 and 7")
        if self.repeat_volume_ratio < 0 or self.sector_breadth_ratio <= 0:
            raise ValueError("volume and sector ratios must be positive")
        if not 0 <= self.bearish_rsi_min <= self.bearish_rsi_max <= 100:
            raise ValueError("bearish RSI bounds must be ordered within 0..100")
        if not 0 <= self.bullish_rsi_min <= self.bullish_rsi_max <= 100:
            raise ValueError("bullish RSI bounds must be ordered within 0..100")
        return self


WEIGHTS: dict[str, float] = {
    "rvol": 20.0,
    "candle": 10.0,
    "liquidity": 10.0,
    "orb": 15.0,
    "entry_risk": 10.0,
    "breadth": 10.0,
    "trend_50dma": 5.0,
    "vwap": 5.0,
    "pdh_pdl": 5.0,
    "repeat_volume": 3.0,
    "follow_through": 4.0,
    "rsi": 1.0,
    "sector": 2.0,
}
assert sum(WEIGHTS.values()) == 100.0


def _boolean_component(
    name: str,
    value: bool | None,
    rule: str,
) -> dict[str, Any]:
    weight = WEIGHTS[name]
    return {
        "name": name,
        "weight": weight,
        "earned": weight if value is True else 0.0,
        "status": "pass" if value is True else "fail" if value is False else "unknown",
        "rule": rule,
    }


def _repeat_volume(signal: LeaderSignal, config: OpeningDecisionConfig) -> bool | None:
    if signal.orb_cumulative_volume is None or signal.opening_volume <= 0:
        return None
    post_open = max(0.0, signal.orb_cumulative_volume - signal.opening_volume)
    return post_open / signal.opening_volume >= config.repeat_volume_ratio


def _follow_through(signal: LeaderSignal) -> bool | None:
    if signal.move_1pct_within_60m is True:
        return True
    if signal.hold_5m_status is ValidationState.PASS:
        return True
    if (
        signal.hold_5m_status is ValidationState.FAIL
        or signal.move_1pct_within_60m is False
    ):
        return False
    return None


def _rsi_alignment(
    signal: LeaderSignal,
    config: OpeningDecisionConfig,
) -> bool | None:
    if signal.rsi_14_1m is None:
        return None
    if signal.direction is LeaderDirection.UP:
        return config.bullish_rsi_min <= signal.rsi_14_1m <= config.bullish_rsi_max
    if signal.direction is LeaderDirection.DOWN:
        return config.bearish_rsi_min <= signal.rsi_14_1m <= config.bearish_rsi_max
    return False


def build_opening_decision(
    signal: LeaderSignal,
    *,
    breadth_alignment: str,
    market_context: dict[str, Any],
    sector_alignment: bool | None = None,
    config: OpeningDecisionConfig | None = None,
) -> dict[str, Any]:
    """Return a bounded score, seven-factor conviction, and Box X/Y state."""

    config = (config or OpeningDecisionConfig()).validate()
    components: list[dict[str, Any]] = []

    rvol_points = {
        LeaderTier.WEAK: 0.0,
        LeaderTier.WATCH: 6.0,
        LeaderTier.SPURT: 12.0,
        LeaderTier.STRONG: 16.0,
        LeaderTier.EXPLOSIVE: 20.0,
    }[signal.tier]
    components.append(
        {
            "name": "rvol",
            "weight": WEIGHTS["rvol"],
            "earned": rvol_points,
            "status": "pass" if signal.is_leader else "fail",
            "rule": "WEAK=0, WATCH=6, SPURT=12, STRONG=16, EXPLOSIVE=20",
        }
    )
    candle_points = {
        CandleQuality.WEAK: 0.0,
        CandleQuality.MODERATE: 6.0,
        CandleQuality.STRONG: 10.0,
    }[signal.candle_quality]
    components.append(
        {
            "name": "candle",
            "weight": WEIGHTS["candle"],
            "earned": candle_points,
            "status": "pass" if candle_points else "fail",
            "rule": "weak=0, moderate=6, strong=10",
        }
    )
    components.append(
        _boolean_component(
            "liquidity",
            True
            if signal.liquidity_state is LiquidityState.PASS
            else False
            if signal.liquidity_state is LiquidityState.FAIL
            else None,
            "price and 20-session turnover Layer-1 gate",
        )
    )
    orb_points = 0.0
    if signal.orb_aligned:
        orb_points += 9.0
    if signal.orb_immediate:
        orb_points += 3.0
    if signal.orb_fresh:
        orb_points += 3.0
    components.append(
        {
            "name": "orb",
            "weight": WEIGHTS["orb"],
            "earned": orb_points,
            "status": "pass" if signal.orb_aligned and signal.orb_fresh else "fail",
            "rule": "aligned=9, immediate=3, fresh<=5m=3",
        }
    )
    if signal.chase_state is ChaseState.NO_ALIGNED_BREAK:
        entry_component = {
            "name": "entry_risk",
            "weight": WEIGHTS["entry_risk"],
            "earned": 0.0,
            "status": "fail",
            "rule": "distance<=0.5%=6 and stop<=1.5%=4",
        }
    elif signal.stop_too_wide is None:
        entry_component = {
            "name": "entry_risk",
            "weight": WEIGHTS["entry_risk"],
            "earned": 0.0,
            "status": "unknown",
            "rule": "distance<=0.5%=6 and stop<=1.5%=4",
        }
    else:
        distance_points = (
            6.0
            if signal.chase_state in {ChaseState.RETEST, ChaseState.PREFERRED}
            else 3.0
            if signal.chase_state is ChaseState.CAUTION
            else 0.0
        )
        entry_component = {
            "name": "entry_risk",
            "weight": WEIGHTS["entry_risk"],
            "earned": distance_points + (0.0 if signal.stop_too_wide else 4.0),
            "status": "pass"
            if distance_points == 6.0 and not signal.stop_too_wide
            else "fail",
            "rule": "distance<=0.5%=6, 0.5..1%=3, >1%=0; stop<=1.5%=4",
        }
    components.append(entry_component)
    breadth_value = (
        True
        if breadth_alignment == "aligned"
        else False
        if breadth_alignment == "against"
        else None
    )
    breadth_component = _boolean_component(
        "breadth",
        breadth_value,
        "aligned=10, neutral=5, against=0",
    )
    if breadth_alignment == "neutral":
        breadth_component.update(earned=5.0, status="neutral")
    components.append(breadth_component)

    repeat_volume = _repeat_volume(signal, config)
    follow_through = _follow_through(signal)
    rsi_aligned = _rsi_alignment(signal, config)
    convictions: dict[str, bool | None] = {
        "trend_50dma": market_context.get("trend_50dma_aligned"),
        "vwap": signal.vwap_aligned,
        "pdh_pdl": signal.pdh_pdl_break_aligned,
        "repeat_volume": repeat_volume,
        "follow_through": follow_through,
        "rsi": rsi_aligned,
        "sector": sector_alignment,
    }
    conviction_rules = {
        "trend_50dma": "directional close versus prior-completed 50-DMA",
        "vwap": "latest completed close aligned with intraday VWAP",
        "pdh_pdl": "session has directionally breached previous-day high/low",
        "repeat_volume": (
            "post-open cumulative volume by first ORB break >= "
            f"{config.repeat_volume_ratio:.2f}x opening volume"
        ),
        "follow_through": "five-minute ORB hold or +1% move within 60 minutes",
        "rsi": (
            f"UP RSI {config.bullish_rsi_min:g}..{config.bullish_rsi_max:g}; "
            f"DOWN RSI {config.bearish_rsi_min:g}..{config.bearish_rsi_max:g}"
        ),
        "sector": f"directional sector advance/decline ratio >= {config.sector_breadth_ratio:g}x",
    }
    for name, value in convictions.items():
        components.append(_boolean_component(name, value, conviction_rules[name]))

    lower_bound = round(sum(float(item["earned"]) for item in components), 2)
    unknown_weight = sum(
        float(item["weight"]) for item in components if item["status"] == "unknown"
    )
    upper_bound = round(min(100.0, lower_bound + unknown_weight), 2)
    coverage = round(100.0 - unknown_weight, 2)
    pass_count = sum(value is True for value in convictions.values())
    known_count = sum(value is not None for value in convictions.values())

    score_trade = lower_bound >= config.trade_score
    score_special = lower_bound >= config.special_score
    box_x = bool(
        signal.is_leader
        and signal.liquidity_state is LiquidityState.PASS
        and signal.candle_quality is not CandleQuality.WEAK
        and market_context.get("trend_50dma_aligned") is True
        and signal.vwap_aligned is True
        and breadth_alignment != "against"
        and score_trade
    )
    box_y = bool(
        box_x
        and signal.orb_aligned
        and signal.orb_fresh
        and signal.chase_state in {ChaseState.RETEST, ChaseState.PREFERRED}
        and signal.stop_too_wide is False
        and pass_count >= config.conviction_required
        and not signal.third_day_repeat
    )
    sterling_combo = bool(box_y and signal.orb_immediate)

    return {
        "model": MODEL_ID,
        "model_id": MODEL_ID,
        "provenance": "Sterling-owned transparent replacement; not ORION proprietary parity",
        "parity_target": "ORION",
        "parity_status": "partial_observable_only",
        "score": {
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "coverage_pct": coverage,
            "trade_threshold": config.trade_score,
            "special_threshold": config.special_score,
            "trade": score_trade,
            "special": score_special,
            "components": components,
        },
        "conviction": {
            "passed": pass_count,
            "known": known_count,
            "total": 7,
            "required": config.conviction_required,
            "factors": convictions,
            "rules": conviction_rules,
        },
        "momentum": {
            "box_x": box_x,
            "box_y": box_y,
            "state": "ready" if box_y else "setup" if box_x else "blocked",
            "box_x_rule": "leader + liquidity + candle + 50-DMA + VWAP + breadth + score>=55",
            "box_y_rule": (
                "Box X + fresh aligned ORB + no chase/wide stop + "
                "conviction>=5 + no day-3 trap"
            ),
        },
        "sterling_combo": sterling_combo,
        "combo_rule": "Box Y with an aligned first ORB break in the 09:16 candle",
        "sterling_execution_eligible": sterling_combo and score_trade,
        "execution_eligible": sterling_combo and score_trade,
    }
