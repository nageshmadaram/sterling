"""Fail-closed historical option walk-forward for ORB.

A corpus must already contain option bars. This module will not invent option
trades from underlying points, and it will not report edge from an empty or
incomplete file.

Two shapes:

* ``bars`` + ``signals`` — pre-labeled option trades (existing).
* ``underlying_bars`` + ``option_bars`` — the engine labels from the
  underlying; option P&L comes only from the option series. A signal with no
  option bar at that timestamp is a rejection, not a synthesized premium.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.engines.nifty_orb_option_replay import (
    OptionBar,
    ReplayAdmission,
    ReplayCostConfig,
    ReplayRejection,
    ReplayTrade,
    replay_signal,
    summarize_replay,
)
from app.engines.nifty_orb_validation import require_historical_option_fields, walk_forward

IST = ZoneInfo("Asia/Kolkata")


def _as_ist(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def _bar(row: dict[str, Any]) -> OptionBar:
    ts = row["timestamp"]
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return OptionBar(
        timestamp=dt,
        symbol=str(row["symbol"]),
        option_type=str(row["option_type"]),
        strike=float(row["strike"]),
        expiry=str(row["expiry"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        bid=float(row.get("bid") or 0),
        ask=float(row.get("ask") or 0),
        volume=float(row.get("volume") or 0),
        open_interest=float(row.get("open_interest") or 0),
        lot_size=int(row["lot_size"]),
    )


def _underlying_bar(row: dict[str, Any]):
    from app.engines.nifty_orb_options import Bar

    ts = row["timestamp"]
    if not isinstance(ts, datetime):
        ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return Bar(
        timestamp=ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume") or 0),
    )


def evaluate_historical_corpus(
    payload: dict[str, Any],
    *,
    train_size: int = 4,
    test_size: int = 2,
    step: int | None = None,
) -> dict[str, Any]:
    """Replay option signals. Dispatches on corpus shape.

    ``underlying_bars`` without ``option_bars`` is a refusal — that is the
    path that used to invent premium from Nifty points.
    """
    if payload.get("underlying_bars") is not None:
        return evaluate_engine_on_option_corpus(payload)
    bars_raw = payload.get("bars")
    if not isinstance(bars_raw, list):
        raise ValueError("corpus.bars must be a list of option OHLC rows")
    require_historical_option_fields(bars_raw)
    signals = payload.get("signals")
    if not isinstance(signals, list) or not signals:
        raise ValueError(
            "corpus has option bars but no labeled signals; refusing to invent option trades"
        )
    bars = [_bar(row) for row in bars_raw]
    costs = ReplayCostConfig()
    admission = ReplayAdmission()

    def evaluator(train: list, test: list) -> dict[str, Any]:
        trades: list[ReplayTrade] = []
        rejections: list[dict[str, Any]] = []
        for item in test:
            outcome = replay_signal(
                bars,
                int(item["entry_index"]),
                float(item["risk_points"]),
                float(item.get("target_r") or 2),
                costs,
                lots=int(item.get("lots") or 1),
                admission=admission,
            )
            if isinstance(outcome, ReplayRejection):
                rejections.append({"entry_index": outcome.signal_index, "reason": outcome.reason})
            else:
                trades.append(outcome)
        return {
            "metrics": summarize_replay(trades),
            "rejections": rejections,
            "train_signals": len(train),
            "test_signals": len(test),
            "option_pnl": True,
        }

    folds = walk_forward(signals, evaluator, train_size=train_size, test_size=test_size, step=step)
    oos_trades = sum(int(f["metrics"]["trades"]) for f in folds)
    oos_net = sum(float(f["metrics"]["net_pnl"]) for f in folds)
    return {
        "folds": folds,
        "fold_count": len(folds),
        "oos_trades": oos_trades,
        "oos_net_pnl": oos_net,
        "option_pnl": True,
        "unattended_live_eligible": False,
        "note": "Walk-forward of labeled option signals. Not evidence of edge until a real multi-month corpus is green out of sample.",
    }


def evaluate_engine_on_option_corpus(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate ORB signals from the underlying; price them from option bars.

    A firing signal with no option bar at that timestamp is a rejection.
    ``unattended_live_eligible`` is always False — this is measurement, not a
    go-live certificate.
    """
    from app.engines.nifty_orb_options import OptionContract, StrategyConfig, generate_signal, select_option

    raw_u = payload.get("underlying_bars")
    raw_o = payload.get("option_bars")
    if not isinstance(raw_u, list) or not raw_u:
        raise ValueError("corpus.underlying_bars must be a list of underlying OHLCV rows")
    if not isinstance(raw_o, list) or not raw_o:
        raise ValueError(
            "corpus.option_bars required; refusing to invent option P&L from underlying points"
        )
    require_historical_option_fields(raw_o)
    cfg = StrategyConfig()
    underlying = [_underlying_bar(row) for row in raw_u]
    option_bars = [_bar(row) for row in raw_o]
    chain_at: dict[datetime, list[OptionBar]] = defaultdict(list)
    series: dict[str, list[OptionBar]] = defaultdict(list)
    for ob in option_bars:
        minute = _as_ist(ob.timestamp).replace(second=0, microsecond=0)
        chain_at[minute].append(ob)
        series[ob.symbol].append(ob)
    for seq in series.values():
        seq.sort(key=lambda b: b.timestamp)

    labeled: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for i in range(3, len(underlying)):
        bars = underlying[: i + 1]
        try:
            sig = generate_signal(bars, cfg)
        except ValueError:
            continue
        if sig.direction == "NONE":
            continue
        minute = _as_ist(underlying[i].timestamp).replace(second=0, microsecond=0)
        chain = chain_at.get(minute) or []
        if not chain:
            rejections.append({"entry_index": i, "reason": "no option bar at signal time", "direction": sig.direction})
            continue
        contracts = [
            OptionContract(
                symbol=ob.symbol,
                strike=ob.strike,
                expiry=ob.expiry,
                option_type=ob.option_type,
                ltp=ob.close,
                bid=ob.bid,
                ask=ob.ask if ob.ask > 0 else ob.close,
                lot_size=ob.lot_size,
                volume=ob.volume,
                open_interest=ob.open_interest,
                quote_timestamp=ob.timestamp,
            )
            for ob in chain
        ]
        try:
            option = select_option(
                underlying[i].close, sig.direction, contracts, cfg, today=minute.date(),
            )
        except (ValueError, RuntimeError) as exc:
            rejections.append({"entry_index": i, "reason": str(exc), "direction": sig.direction})
            continue
        seq = series[option.symbol]
        entry_index = next(
            (
                j
                for j, b in enumerate(seq)
                if _as_ist(b.timestamp).replace(second=0, microsecond=0) == minute
            ),
            None,
        )
        if entry_index is None:
            rejections.append({"entry_index": i, "reason": "selected contract has no bar at signal time"})
            continue
        labeled.append({
            "entry_index": entry_index,
            "symbol": option.symbol,
            "risk_points": max(sig.atr * cfg.stop_buffer_atr, 1.0),
            "target_r": cfg.target_r,
            "lots": 1,
            "series": seq,
        })

    costs = ReplayCostConfig()
    admission = ReplayAdmission()
    trades: list[ReplayTrade] = []
    for item in labeled:
        outcome = replay_signal(
            item["series"],
            int(item["entry_index"]),
            float(item["risk_points"]),
            float(item["target_r"]),
            costs,
            lots=int(item["lots"]),
            admission=admission,
        )
        if isinstance(outcome, ReplayRejection):
            rejections.append({"entry_index": outcome.signal_index, "reason": outcome.reason, "symbol": item["symbol"]})
        else:
            trades.append(outcome)

    return {
        "trades": len(trades),
        "metrics": summarize_replay(trades),
        "rejections": rejections,
        "engine_signals": len(labeled) + sum(1 for r in rejections if r.get("reason") == "no option bar at signal time"),
        "option_pnl": True,
        "unattended_live_eligible": False,
        "note": "Engine-labeled option replay. Not evidence of edge until a real multi-month corpus is green out of sample.",
    }