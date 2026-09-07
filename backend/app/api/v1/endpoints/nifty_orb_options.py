"""NIFTY ORB option-level replay and universe scan endpoints.

NOTE: this router is not currently mounted -- the live ORB routes are served
under ``/api/v1/config/nifty-orb-options``. It is kept auth-correct anyway, so
that mounting it later cannot introduce a tenant-isolation hole.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import UserContext, get_current_user

from app.engines.nifty_orb_option_replay import (
    OptionBar,
    ReplayAdmission,
    ReplayCostConfig,
    ReplayRejection,
    replay_signal,
    summarize_replay,
)
from app.engines.nifty_orb_validation import TradingCosts
from app.engines.nifty_orb_options import StrategyConfig

router = APIRouter(prefix="/nifty-orb-options", tags=["nifty-orb-options"])


def _option_bar(row: dict) -> OptionBar:
    ts = row.get("timestamp")
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
        lot_size=int(row.get("lot_size") or 1),
    )


@router.post("/replay")
async def replay(body: dict) -> dict:
    """Replay option-buy trades against real option bars.

    ``entry_index`` names the bar whose close produced the signal; the fill
    lands ``entry_delay_bars`` later (default 1), so no trade is priced off
    information the strategy did not have. Signals that could not be traded are
    returned under ``rejections`` rather than dropped, because a replay that
    silently discards untradeable signals reports a strategy nobody ran.
    """
    rows = body.get("bars")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(422, "bars must be a non-empty option OHLC list")
    try:
        bars = [_option_bar(row) for row in rows]
        costs = ReplayCostConfig(
            slippage_points=float(body.get("slippage_points") or 0),
            brokerage_per_order=float(body.get("brokerage_per_order") or 0),
            charges_per_order=float(body.get("charges_per_order") or 0),
            statutory=TradingCosts(**body["statutory_costs"]) if body.get("statutory_costs") else None,
        )
        admission = ReplayAdmission(**body["admission"]) if body.get("admission") else ReplayAdmission()
        entry_delay = int(body.get("entry_delay_bars") or 1)
        trades, rejections = [], []
        for item in body.get("trades", []):
            outcome = replay_signal(
                bars,
                int(item["entry_index"]),
                float(item["risk_points"]),
                float(item.get("target_r") or 2),
                costs,
                lots=int(item.get("lots") or 1),
                admission=admission,
                entry_delay_bars=entry_delay,
            )
            (rejections if isinstance(outcome, ReplayRejection) else trades).append(outcome)
        return {
            "trades": [trade.to_dict() for trade in trades],
            "rejections": [{"entry_index": r.signal_index, "reason": r.reason} for r in rejections],
            "metrics": summarize_replay(trades),
            "option_pnl": True,
            "entry_delay_bars": entry_delay,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/scan")
async def scan(body: dict, user: UserContext = Depends(get_current_user)) -> dict:
    """Scan a bounded ORB universe and return ranked actionable signals.

    The tenant comes from the authenticated session, never from the body. Taking
    `user_id` from the request let any caller scan as any user -- reading their
    configured universe and issuing broker calls against their Kite credentials
    and rate limit.
    """
    uid = str(user.user_id or "").strip()
    if not uid:
        raise HTTPException(401, "authenticated user is required")
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_nifty_orb_signals_response()
    try:
        raw = body.get("config") or {}
        cfg = StrategyConfig(**raw)
        if cfg.data_source != "kite":
            raise ValueError("universe scan currently requires data_source='kite'")
        from app.services.nifty_orb_universe_runtime import scan_kite_universe
        results = await scan_kite_universe(
            uid,
            cfg,
            max_candidates=min(int(body.get("max_candidates") or 30), 100),
            concurrency=min(int(body.get("concurrency") or 6), 8),
        )
        return {
            "count": len(results),
            "signals": [
                {
                    "symbol": item.instrument.symbol,
                    "kind": item.instrument.kind,
                    "direction": item.signal.direction,
                    "regime": item.signal.regime,
                    "confidence": item.signal.confidence,
                    "timestamp": item.signal.timestamp.isoformat() if item.signal.timestamp else None,
                    "or_high": item.signal.or_high,
                    "or_low": item.signal.or_low,
                    "vwap": item.signal.vwap,
                    "atr": item.signal.atr,
                    "breakout_distance": item.signal.breakout_distance,
                    "volume_ratio": item.signal.volume_ratio,
                    "reason": item.signal.reason,
                }
                for item in results
            ],
        }
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
