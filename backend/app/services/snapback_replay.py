"""Daily replay positions settled by the same engine as Snapback history.

Only a completed session is handed to the exit engine. Positions retain their
last daily valuation intraday; partial bars cannot advance their horizon.
"""
from dataclasses import replace
from datetime import datetime

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
