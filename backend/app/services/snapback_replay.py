"""Daily replay positions settled by the same engine as Snapback history.

Only a completed session is handed to the exit engine. Positions retain their
last daily valuation intraday; partial bars cannot advance their horizon.
"""
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from app.engines.option_contracts import spec_for
from app.engines.snapback import features, to_bars
from app.engines.snapback.backtest import CostModel, _run_one, _value
from app.engines.snapback.hedge import FuturesCost, rolling_beta
from app.engines.snapback.models import IST
from app.engines.snapback.regime import gate_for


def cost_for(runner, symbol):
    from app.services.simulation import _apply_friction
    buy, _, _ = _apply_friction(100, 100, symbol, runner._config, strategy='snapback')
    if runner._config.friction_mode == 'ideal':
        return CostModel(brokerage_per_order=0, stt_sell_pct=0, exchange_pct=0,
                         gst_pct=0, misc_pct=0, slippage_pct=0)
    return CostModel(slippage_pct=buy - 100)


def entry_ready(runner, symbol, bar, cfg):
    if cfg.hedge_mode == 'none':
        return True
    from app.services.simulation import _asof_symbol_bars, _snapback_daily_tape
    asof = bar['time']
    market_rows = _asof_symbol_bars(getattr(runner, '_snapback_market_bars', None)
                                   or getattr(runner, '_candles', []), 'NIFTY', asof)
    session_nifty = getattr(runner, '_session_bars', {}).get('NIFTY') or []
    if session_nifty:
        m_times = {b['time'] for b in market_rows if isinstance(b, dict) and 'time' in b}
        market_rows = list(market_rows) + [b for b in session_nifty if isinstance(b, dict) and b.get('time', 0) <= asof and b.get('time') not in m_times]
    has_same_time = any(isinstance(b, dict) and b.get('time') == asof for b in market_rows)
    if not has_same_time:
        if isinstance(bar, dict) and bar.get('symbol') == 'NIFTY':
            market_rows = list(market_rows) + [bar]
        else:
            from app.services.ohlcv_store import get_candles_bulk
            res = getattr(getattr(runner, '_config', None), 'resolution', None) or '5m'
            nifty_bulk = get_candles_bulk(['NIFTY'], res, limit_per_symbol=1, since=asof, until=asof + 300)
            n_bars = nifty_bulk.get('NIFTY', [])
            if n_bars:
                nb = dict(n_bars[0])
                nb['symbol'] = 'NIFTY'
                market_rows = list(market_rows) + [nb]
    market = to_bars(_snapback_daily_tape('NIFTY', market_rows, asof, runner))
    stock = to_bars(_snapback_daily_tape(symbol, [bar], asof, runner))
    day = datetime.fromtimestamp(asof, IST).date().isoformat()
    if day not in rolling_beta(stock, market):
        runner._strategy_notes['snapback'] = f'{symbol}: required NIFTY history or opening observation missing; hedged entry blocked'
        return False
    return True




def attach(runner, trade, signal, cfg, leg, at):
    from app.services.simulation import _snapback_daily_tape
    rows = _snapback_daily_tape(trade.underlying, [], at.timestamp(), runner)
    signal_candle = signal.get('snapback_signal_candle')
    if signal_candle is not None:
        rows = [r for r in rows if datetime.fromtimestamp(r['time'], IST).date().isoformat() < signal['snapback_day']]
        rows.append(dict(signal_candle))
    indices = [i for i, b in enumerate(rows)
               if datetime.fromtimestamp(b['time'], IST).date().isoformat() == signal['snapback_day']]
    if not indices:
        raise ValueError('Snapback entry has no confirmed signal session')
    model_cfg = replace(cfg, sizing_mode='LOTS', lots=trade.lots,
                        max_lots=max(cfg.max_lots, trade.lots))
    positions = getattr(runner, '_snapback_positions', None)
    if positions is None:
        positions = runner._snapback_positions = {}
    positions[trade.trade_id] = {
        'rows': [dict(b) for b in rows], 'index': indices[-1],
        'cfg': model_cfg, 'iv': signal['snapback_iv'], 'side': signal['snapback_side'],
        'cost': cost_for(runner, trade.underlying),
    }
    trade.premium_model = {
        'iv': signal['snapback_iv'], 'dte': cfg.min_dte, 'sessions': 0,
        'short_strike': leg.get('short_strike', 0),
        'smile_slope': cfg.smile_slope, 'smile_itm_slope': cfg.smile_itm_slope,
    }
    # Keep entry, exits and their cost model in the same units and precision.
    trade.raw_entry = value(trade, trade.spot_entry)
    trade.entry_price = positions[trade.trade_id]['cost'].fill(trade.raw_entry, 'buy')
    trade.stop_loss = trade.entry_price * (1 - cfg.premium_stop_pct / 100)
    trade.target_price = value(trade, trade.spot_target)
    trade.slippage = round((trade.entry_price - trade.raw_entry) * trade.quantity, 2)
    trade.mark_price = trade.entry_price
    trade.raw_mark = trade.raw_entry
    if getattr(trade, 'leg_delta', None) is None:
        trade.leg_delta = leg.get('delta') or 0.71


def value(trade, spot):
    m = trade.premium_model
    return float(_value(spot, trade.strike, m['short_strike'],
                        max(m['dte'] - m['sessions'], 1) / 365,
                        m['iv'], trade.opt_type == 'CE', m['smile_slope'],
                        trade.spot_entry, m['smile_itm_slope']))


def settle(runner, trade, candle, at):
    """Update one position from a closed daily prefix, with no future rows."""
    from app.services.simulation import _asof_symbol_bars, _snapback_daily_tape
    p = runner._snapback_positions[trade.trade_id]
    day = at.date().isoformat()
    p['rows'] = [r for r in p['rows']
                 if datetime.fromtimestamp(r['time'], IST).date().isoformat() < day]
    p['rows'].append(dict(candle))
    bars = to_bars(p['rows'])
    cfg = p['cfg']
    market_rows = _asof_symbol_bars(getattr(runner, '_snapback_market_bars', None)
                                   or getattr(runner, '_candles', []), 'NIFTY', at.timestamp())
    market_daily = _snapback_daily_tape('NIFTY', market_rows, at.timestamp(), runner)
    if cfg.market_filter != 'off' or cfg.hedge_mode != 'none':
        # An intraday preview cannot stand in for a required market CLOSE.
        from app.services.simulation import _store_daily_sessions
        closed_market = [b['daily_candle'] for b in market_rows if b.get('daily_candle')]
        if not closed_market:
            closed_market = _store_daily_sessions('NIFTY', at.timestamp())
        current = next((b for b in reversed(closed_market)
                        if datetime.fromtimestamp(b['time'], IST).date().isoformat() == day), None)
        if current is None:
            runner._strategy_notes['snapback'] = f'NIFTY close missing for {day}; daily valuation blocked'
            return
        market_daily = [b for b in market_daily
                        if datetime.fromtimestamp(b['time'], IST).date().isoformat() < day] + [current]
    market = to_bars(market_daily)
    gate = gate_for({'NIFTY': market}, market_filter=cfg.market_filter, ema_period=cfg.market_ema)
    marks = {market.day(i): float(market.close[i]) for i in range(len(market))}
    notes = []
    result = _run_one(trade.underlying, bars, features(bars, cfg),
                      np.full(len(bars), p['iv']), cfg, p['cost'], p['index'],
                      p['side'], spec_for(trade.underlying), notes.append,
                      beta_by_day=rolling_beta(bars, market) if cfg.hedge_mode != 'none' else None,
                      market_at=marks, fut_cost=(FuturesCost(brokerage_per_order=0, stt_sell_pct=0, exchange_pct=0,
                                            gst_pct=0, misc_pct=0, slippage_pct=0, carry_pct=0)
                                if runner._config.friction_mode == 'ideal' else FuturesCost()), gate=gate)
    if result is None:
        runner._strategy_notes['snapback'] = '; '.join(notes) or 'Daily valuation unavailable'
        return
    if notes:
        runner._strategy_notes['snapback'] = '; '.join(notes)
    trade.premium_model['sessions'] = result.held_days
    trade.bars_held = result.held_days * runner._bars_per_session()
    trade.duration_mins = result.held_days * runner.SESSION_MINUTES
    trade.mark_price = round(result.fill_out, 2)
    trade.raw_mark = round(result.premium_out, 2)
    trade.pnl_usd = result.net
    trade.pnl_pct = round(result.ret * 100, 2)
    trade.fees = result.costs
    trade.hedge_pnl = -result.market_pnl - result.hedge_cost
    trade.slippage = round(((result.fill_in - result.premium_in)
                            + (result.premium_out - result.fill_out)) * result.qty, 2)
    if result.reason != 'tape_ended':
        trade.raw_exit = result.premium_out
        trade.exit_price = result.fill_out
        trade.mark_price = result.fill_out
        trade.raw_mark = result.premium_out
        trade.exit_reason = result.reason.upper()
        trade.exit_timestamp_ms = int(at.timestamp() * 1000)
        trade.exit_time_iso = at.strftime('%Y-%m-%dT%H:%M:%S') if getattr(runner, '_is_multi_day', False) else at.strftime('%H:%M:%S')
        trade.status = 'WIN' if result.net > 0 else 'LOSS'
        runner._active_until_bar.pop((trade.underlying, 'snapback'), None)
    runner._publish('trade', trade.model_dump())
    runner._recompute_totals()


def run_intraday_causal_replay(
    spot_bars: list[dict],
    option_quotes: list[dict],
    cfg: Any,
    symbol: str = "NIFTY",
    lot_size: int = 50,
    available_cash: float = 100000.0,
) -> dict[str, Any]:
    """Execute causal intraday replay over spot bars and option quotes.

    Enforces A0-A5 pipeline:
    - Causal feature extraction and 30-bar contiguous warmup.
    - Setup decision based on Bollinger stretch + re-entry.
    - Cost estimate and target feasibility ceiling check.
    - Durable risk reservation.
    - Deterministic position lifecycle state machine.
    """
    from app.engines.snapback.intraday_features import build_feature_snapshot
    from app.engines.snapback.intraday_models import SetupDecision
    from app.engines.snapback.intraday_economics import build_trade_plan
    from app.engines.snapback.intraday_lifecycle import create_initial_position, advance_position_lifecycle
    from app.services.snapback_execution import AccountRiskManager

    risk_mgr = AccountRiskManager()
    trades: list[dict] = []
    opportunities: int = 0
    rejections: list[str] = []

    active_position = None
    remaining_day_loss = available_cash * (getattr(cfg, "scalp_daily_loss_pct", 2.0) / 100.0)

    for i in range(30, len(spot_bars)):
        prefix = spot_bars[: i + 1]
        feat = build_feature_snapshot(prefix, cfg, min_warmup_bars=30)
        if feat is None:
            continue

        bar = spot_bars[i]
        ts_ms = feat.timestamp_ms

        # Position management if position is active
        if active_position is not None:
            from app.services.causal_series import latest_asof
            quote = latest_asof(option_quotes, ts_ms, max_age_ms=60000)
            current_bid = quote.get("bid", active_position.desired_stop) if quote else active_position.desired_stop
            
            active_position, exit_reason = advance_position_lifecycle(
                active_position, current_bid, bar, cfg, now_ms=ts_ms
            )

            if exit_reason is not None or active_position.active_phase == "EXIT_REQUIRED":
                exit_price = current_bid
                gross_pnl = (exit_price - active_position.entry_vwap) * active_position.confirmed_quantity
                net_pnl = gross_pnl - getattr(cfg, "scalp_fixed_cost_inr", 40.0)

                trades.append({
                    "position_id": active_position.position_id,
                    "symbol": symbol,
                    "side": active_position.side,
                    "entry_price": active_position.entry_vwap,
                    "exit_price": exit_price,
                    "quantity": active_position.confirmed_quantity,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "exit_reason": exit_reason or "exit_required",
                    "bars_held": active_position.bars_held,
                })
                active_position = None
            continue

        # Evaluate entry setup
        prev_bar = spot_bars[i - 1]
        up_reentry = prev_bar["close"] > feat.upper_band and feat.close <= feat.upper_band
        down_reentry = getattr(cfg, "allow_fade_down", False) and prev_bar["close"] < feat.lower_band and feat.close >= feat.lower_band

        if not (up_reentry or down_reentry):
            continue

        opportunities += 1
        side = "PE" if up_reentry else "CE"

        setup = SetupDecision(
            setup_id=f"setup_{ts_ms}",
            timestamp_ms=ts_ms,
            symbol=symbol,
            side=side,
            trigger_price=feat.close,
            mean_target=feat.mean,
            invalidation_reference=feat.upper_band if up_reentry else feat.lower_band,
            band_reentry_confirmed=True,
            is_eligible=True,
        )

        from app.services.causal_series import latest_asof
        quote = latest_asof(option_quotes, ts_ms, max_age_ms=60000)
        ask_entry = quote.get("ask", 100.0) if quote else 100.0
        bid_exit = quote.get("bid", 99.0) if quote else 99.0

        plan = build_trade_plan(
            setup=setup,
            contract_symbol=f"{symbol}_{side}",
            ask_entry=ask_entry,
            bid_exit=bid_exit,
            lot_size=lot_size,
            cfg=cfg,
            available_cash=available_cash,
            remaining_day_loss=remaining_day_loss,
            now_ms=ts_ms,
        )

        if plan.quantity <= 0 or plan.feasibility_status != "FEASIBLE":
            rejections.append(f"Plan rejected: feasibility={plan.feasibility_status}")
            continue

        res = risk_mgr.reserve_risk(
            plan=plan,
            account_id="sim_acc",
            session_id="sim_sess",
            cfg=cfg,
            available_cash=available_cash,
            settled_session_loss=sum(abs(t["net_pnl"]) for t in trades if t["net_pnl"] < 0),
            open_positions_risk=0.0,
            now_ms=ts_ms,
        )

        if res is None:
            rejections.append("Risk reservation failed")
            continue

        active_position = create_initial_position(
            position_id=f"pos_{ts_ms}",
            account_id="sim_acc",
            contract_symbol=plan.selected_contract_symbol,
            side=side,
            confirmed_quantity=plan.quantity,
            entry_vwap=ask_entry,
            cfg=cfg,
            now_ms=ts_ms,
        )

    total_net = sum(t["net_pnl"] for t in trades)
    win_count = sum(1 for t in trades if t["net_pnl"] > 0)
    win_rate = (win_count / len(trades)) if trades else 0.0

    return {
        "symbol": symbol,
        "opportunities": opportunities,
        "completed_trades": len(trades),
        "trades": trades,
        "total_net_pnl": round(total_net, 2),
        "win_rate": round(win_rate, 4),
        "rejections": rejections,
    }
