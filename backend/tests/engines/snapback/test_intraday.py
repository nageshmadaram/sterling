"""Causal timing, premium economics and conservative fills, not profit claims."""
from dataclasses import replace
from datetime import datetime
import numpy as np
import pytest
from app.engines.snapback import intraday
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.models import Bars, IST, SnapbackSignal

CFG = replace(SnapbackConfig(), trading_mode="scalp")
START = datetime(2026, 9, 14, 9, 15, tzinfo=IST).timestamp()
COLS = ("time", "open", "high", "low", "close", "volume")


def tape(prices, *, start=START, opens=None, highs=None, lows=None):
    c = np.asarray(prices, dtype=float)
    o = np.asarray(opens if opens is not None else c, dtype=float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c)+.1)
    l = np.asarray(lows if lows is not None else np.minimum(o, c)-.1)
    return Bars(start+np.arange(len(c))*60, o, h, l, c, np.full(len(c), 1000.))


def setup():
    c = list(100+np.sin(np.arange(60)*2)*.5)+[104, 100.4]
    o = c.copy()
    o[-1] = c[-2]
    return tape(c, opens=o)


def concat(a, b):
    return Bars(*(np.concatenate((getattr(a, n), getattr(b, n))) for n in COLS))


def schedule(monkeypatch, signals):
    def evaluate(bars, cfg, symbol="", asof=None):
        ts = float(bars.time[-1])
        assert asof == ts+cfg.scalp_timeframe_minutes*60
        assert np.all(bars.time < asof)
        if ts not in signals:
            return []
        opt = signals[ts]
        return [SnapbackSignal(symbol="NIFTY", side="fade_up" if opt == "PE" else "fade_down",
                direction="BEARISH" if opt == "PE" else "BULLISH", option_type=opt,
                timestamp_ms=int(ts*1000), entry=100, mean_target=99, stretch=2, atr=1,
                realized_vol=.2, assumed_iv=.2, level=102, strength="MODERATE")]
    monkeypatch.setattr(intraday, "evaluate_intraday", evaluate)


def replay(spot, opt, cfg=CFG):
    return intraday.replay_intraday(spot, opt, cfg, "NIFTY", option_type="PE", lot_size=50,
                                   option_symbol="NIFTY_TEST_PE")


def opened(start=START):
    return intraday.open_trade(intraday.make_plan(100, 50, CFG), start)


def step(state, o, h, l, c, cfg=CFG):
    return intraday.advance_trade(state, timestamp=state.entry_time+state.bars_held*60,
                                  open=o, high=h, low=l, close=c, cfg=cfg)


def test_closed_signal_and_correct_minute_volatility():
    b = setup()
    s = intraday.evaluate_intraday(b, CFG, "NIFTY", asof=b.time[-1]+60)[0]
    assert s.option_type == "PE" and s.mean_target < s.entry
    assert s.metrics["signal_bar_closed"]
    assert s.realized_vol == pytest.approx(np.std(np.diff(np.log(b.close[-21:])), ddof=1)*np.sqrt(252*375))
    assert "not quoted IV" in s.metrics["volatility_basis"]


def test_forming_stale_or_prior_session_signal_is_rejected():
    b = setup()
    assert intraday.evaluate_intraday(b, CFG, asof=b.time[-1]+60)
    for delta in (59, 120, 86460):
        assert intraday.evaluate_intraday(b, CFG, asof=b.time[-1]+delta) == []


def test_future_prices_even_invalid_ohlc_do_not_change_current_signal():
    b = setup()
    now = b.time[-1]+60
    future = tape([9999], start=now+60, highs=[1])
    assert intraday.evaluate_intraday(concat(b, future), CFG, asof=now) == intraday.evaluate_intraday(b, CFG, asof=now)


def test_misaligned_or_wrong_timeframe_and_gap_warmup_are_rejected():
    b = setup()
    assert intraday.evaluate_intraday(b, replace(CFG, scalp_timeframe_minutes=5), asof=b.time[-1]+60) == []
    shifted = replace(b, time=b.time+10)
    assert intraday.evaluate_intraday(shifted, CFG, asof=shifted.time[-1]+60) == []
    broken = replace(b, time=b.time.copy())
    broken.time[-10:] += 60
    assert intraday.evaluate_intraday(broken, CFG, asof=broken.time[-1]+60) == []


@pytest.mark.parametrize("end", [104, 105, 99])
def test_no_reversal_or_no_remaining_mean_move_no_signal(end):
    b = setup()
    changed = replace(b, close=b.close.copy(), low=b.low.copy(), high=b.high.copy())
    changed.close[-1] = end
    changed.high[-1] = max(changed.open[-1], end)+.1
    changed.low[-1] = min(changed.open[-1], end)-.1
    assert intraday.evaluate_intraday(changed, CFG, asof=changed.time[-1]+60) == []


def test_adx_and_optional_volume_fail_closed():
    b = setup()
    now = b.time[-1]+60
    assert intraday.evaluate_intraday(b, replace(CFG, scalp_max_adx=1), asof=now) == []
    no_vol = replace(b, volume=np.zeros(len(b)))
    assert intraday.evaluate_intraday(no_vol, CFG, asof=now)
    assert intraday.evaluate_intraday(no_vol, replace(CFG, scalp_min_relative_volume=1), asof=now) == []
    assert intraday.evaluate_intraday(b, replace(CFG, scalp_min_relative_volume=2), asof=now) == []


def test_bullish_mirror_requires_opt_in():
    b = setup()
    inv = Bars(b.time, 200-b.open, 200-b.low, 200-b.high, 200-b.close, b.volume)
    assert intraday.evaluate_intraday(inv, CFG, asof=b.time[-1]+60) == []
    s = intraday.evaluate_intraday(inv, replace(CFG, allow_fade_down=True), asof=b.time[-1]+60)
    assert s and s[0].option_type == "CE"


def test_plan_increases_requested_target_to_net_rr_floor():
    p = intraday.make_plan(100, 50, CFG)
    assert p["accepted"] and p["requested_target_points"] == 5
    assert p["effective_target_points"] == pytest.approx(7.6)
    assert p["estimated_stop_loss_inr"] == p["estimated_net_target_inr"] == 290
    assert p["net_reward_risk"] >= CFG.scalp_min_net_rr and p["target_is_soft_trigger"]


def test_cash_premium_allocation_risk_and_lot_caps_never_round_up():
    c = replace(CFG, lots=10000, max_lots=10000)
    p = intraday.make_plan(100, 50, c)
    assert p["lots"] == 1 and p["estimated_stop_loss_inr"] <= p["risk_budget_inr"]
    assert not intraday.make_plan(100, 100000, c)["accepted"]
    assert not intraday.make_plan(100000, 50, c)["accepted"]
    p = intraday.make_plan(100, 50, replace(CFG, sizing_mode="PREMIUM_PCT", premium_pct_of_capital=1))
    assert not p["accepted"] and p["lots"] == 0


def test_spread_floor_fixed_cost_once_and_observed_entry_not_changed_by_tick_rounding():
    p = intraday.make_plan(100, 50, replace(CFG, capital_inr=1_000_000, lots=3), spread_points=2)
    assert p["lots"] == 3 and p["estimated_total_cost_inr"] == 340
    assert p["net_reward_risk"] >= 1
    p = intraday.make_plan(100.013, 50, replace(CFG, capital_inr=58000, lots=2))
    assert p["entry"] == 100.013 and not p["accepted"] and p["lots"] == 0


@pytest.mark.parametrize("premium,lot,spread", [(0, 50, 0), (float("nan"), 50, 0), (100, 0, 0),
                                              (100, 1.5, 0), (100, 50, -1), (4, 50, 0)])
def test_invalid_plan_cannot_be_opened(premium, lot, spread):
    p = intraday.make_plan(premium, lot, CFG, spread)
    assert not p["accepted"]
    with pytest.raises(ValueError):
        intraday.open_trade(p, START)


def test_old_stop_wins_ambiguous_target_bar_and_gap_fills_at_open():
    s = step(opened(), 100, 112, 95, 110)
    assert s.exited and s.exit_price == 96 and s.exit_reason == "stop" and s.net_pnl_inr == -290
    gap = step(opened(), 90, 110, 89, 108)
    assert gap.exit_reason == "gap_stop" and gap.exit_price == 90 and gap.net_pnl_inr == -590


def test_runner_upgrade_is_immutable_and_new_stop_waits_until_next_bar():
    original = opened()
    s = step(original, 100, 110, 99, 109)
    assert s.runner and not s.exited and s.stop == 107
    assert original.stop == 96 and not original.runner
    # That bar's low 99 cannot retroactively hit the newly raised stop107.
    stopped = step(s, 108, 112, 106, 111)
    assert stopped.exit_price == 107 and stopped.exit_reason == "trailing_stop"


def test_trail_never_retreats_and_can_lock_costs_before_target():
    s = step(opened(), 100, 106, 99, 105)
    assert not s.runner and not s.exited and s.stop >= s.profit_lock
    s = step(opened(), 100, 110, 99, 109)
    s2 = step(s, 109, 113, 108, 112)
    s3 = step(s2, 112, 113, 111, 111.5)
    assert not s3.exited and s.stop <= s2.stop == s3.stop


def test_target_touch_without_continuation_uses_actual_close_not_target_fill():
    s = step(opened(), 100, 109, 99, 101)
    assert s.exit_reason == "target_retracement" and s.exit_price == 101 and s.net_pnl_inr == -40


def test_timeout_runner_timeout_eod_and_missing_candle():
    assert step(opened(), 100, 101, 99, 100.5, replace(CFG, scalp_max_hold_bars=1)).exit_reason == "timeout"
    assert step(opened(), 100, 110, 99, 109, replace(CFG, scalp_runner_max_bars=1)).exit_reason == "runner_timeout"
    late = opened(datetime(2026, 9, 14, 15, 14, tzinfo=IST).timestamp())
    assert step(late, 100, 101, 99, 100.5).exit_reason == "session_exit"
    with pytest.raises(ValueError, match="contiguous"):
        intraday.advance_trade(opened(), timestamp=START+60, open=100, high=101, low=99, close=100, cfg=CFG)


def test_actual_signal_replay_enters_next_option_open_not_signal_price():
    b = setup()
    spot = concat(b, tape([100], start=b.time[-1]+60))
    opt = tape([200.]*(len(b)-1)+[400., 120.], highs=[201.]*(len(b)-1)+[500., 121.],
               lows=[199.]*(len(b)-1)+[300., 119.])
    result = replay(spot, opt, replace(CFG, scalp_max_hold_bars=1))
    assert result["trade_count"] == 1
    t = result["trades"][0]
    assert t["entry"] == 120 and t["entry_time"] == b.time[-1]+60 and t["net_pnl_inr"] == -90


def test_contract_side_is_bound(monkeypatch):
    start = START+30*60
    b = tape([100]*4, start=start)
    schedule(monkeypatch, {start: "CE"})
    assert replay(b, b)["trade_count"] == 0


def test_remaining_daily_budget_refuses_next_trade_and_resets_next_day(monkeypatch):
    start = START+30*60
    b = concat(tape([100]*5, start=start), tape([100]*5, start=start+86400))
    opt = replace(b, low=np.full(len(b), 95.))
    schedule(monkeypatch, {float(t): "PE" for t in b.time})
    result = replay(b, opt, replace(CFG, scalp_daily_loss_pct=.5, scalp_cooldown_bars=0))
    # First risk290 leaves210 daily budget: another minimum lot cannot fit.
    assert result["trade_count"] == 2
    assert [t["entry_time"] for t in result["trades"]] == [start+60, start+86400+60]
    assert result["rejected_plans"] > 0


def test_cooldown_daily_entry_cap_and_no_pyramiding(monkeypatch):
    start = START+30*60
    b = tape([100]*16, start=start)
    schedule(monkeypatch, {float(t): "PE" for t in b.time})
    r = replay(b, b, replace(CFG, scalp_max_hold_bars=2, scalp_cooldown_bars=2, scalp_max_trades_per_day=2))
    assert r["trade_count"] == 2 and r["trades"][0]["bars_held"] == 2
    assert r["trades"][1]["entry_time"] >= r["trades"][0]["exit_time"]+2*60


def test_unmatched_option_bars_gaps_and_invalid_ohlc_are_rejected():
    b = tape([100]*5)
    with pytest.raises(ValueError, match="synchronized"):
        replay(b, intraday._slice(b, slice(0, 4)))
    gap = intraday._slice(b, [0, 1, 3, 4])
    with pytest.raises(ValueError, match="gaps"):
        replay(gap, gap)
    with pytest.raises(ValueError, match="valid OHLCV"):
        replay(b, replace(b, high=np.full(5, 90.)))


def test_truncated_open_position_not_counted_as_winner_or_reopened_next_day(monkeypatch):
    start = START+30*60
    b = concat(tape([100]*2, start=start), tape([100]*2, start=start+86400))
    schedule(monkeypatch, {float(t): "PE" for t in b.time})
    r = replay(b, b)
    assert r["trade_count"] == r["net_pnl_inr"] == 0 and not r["complete"]
    assert r["unresolved_position"]["entry_time"] == start+60 and "Previous session" in r["unresolved_reason"]


def test_eod_closes_and_roundtrip_costs_are_charged_once(monkeypatch):
    start = datetime(2026, 9, 14, 15, 12, tzinfo=IST).timestamp()
    b = tape([100, 100, 103], start=start)
    schedule(monkeypatch, {start: "PE"})
    r = replay(b, b, replace(CFG, scalp_entry_end_minute=914))
    t = r["trades"][0]
    assert r["complete"] and t["exit_reason"] == "session_exit"
    assert t["gross_pnl_inr"] == 150 and t["estimated_cost_inr"] == 90 and t["net_pnl_inr"] == 60
