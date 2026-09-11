from datetime import date

import numpy as np
import pytest

from app.domain.models import Candle
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from app.services.kite_engine.scanner import (
    _SIGNAL_RETENTION_MS, KiteEngineScanner, _compile_rows, _copy_prior_leg_snapshot,
    contract_bar_is_current, live_red_counts,
    _prior_leg_snapshots, _retain_signals, _stamp_leg_premium_stops, attach_strikes,
    drop_forming, evaluate_derivative_contract, evaluate_item, option_order_args,
)
from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg
from app.services.kite_engine.strikes import OptionPick
from app.services.kite_engine.universe import UniverseItem


def _candles(close_path, start_ms=0):
    c = np.asarray(close_path, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    out = []
    for i in range(len(c)):
        hi = max(o[i], c[i]) + 1.0
        lo = max(0.01, min(o[i], c[i]) - 1.0)
        out.append(Candle(timestamp_ms=start_ms + i * 3_600_000, open=float(o[i]),
                          high=float(hi), low=float(lo), close=float(c[i]), volume=1.0))
    return out


def test_exit_aligned_trail_never_rests_on_the_wrong_side_of_price():
    """Audit lead 28. `trail_value_for_threshold` returns the threshold line whether or
    not it is still aligned. Once a SuperTrend flips it sits on the OTHER side of price,
    so the stop landed above a long and the next bar "breached" it — at the wrong count:
    under three_red the threshold line is SLOW, and slow can flip while fast and mid are
    still green (one red, not three), so the trade exited immediately under the very
    setting whose purpose is to wait for three.
    """
    from app.engines.sterling_kite_engine import exits

    class _Regime:
        """Slow has flipped; fast and mid are still green — one red under three_red."""

        l_fast = np.array([100.0, 101.0])
        l_mid = np.array([98.0, 99.0])
        l_slow = np.array([95.0, 130.0])   # flipped: now ABOVE a long's price

        def green_lines(self, direction, i):
            return ["fast", "mid"]          # slow is not aligned

        def trail_value_for_threshold(self, i, threshold):
            return float({1: self.l_fast, 2: self.l_mid, 3: self.l_slow}[threshold][i])

        def best_trail_line_value(self, direction, i):
            return float(self.l_mid[i])     # tightest still-aligned

        def line(self, name):
            return {"fast": self.l_fast, "mid": self.l_mid, "slow": self.l_slow}[name]

    cfg = SterlingKiteEngineConfig(exit_mode="three_red", exit_aligned_trail=True)
    level = exits.trail_level(_Regime(), "long", 0, 1, cfg)

    assert level == pytest.approx(99.0), (
        f"trail sat at {level} — the flipped slow line, above the position it is meant "
        f"to protect"
    )


def test_exit_aligned_trail_moves_stop_to_mode_line():
    """D2 wiring: with exit_aligned_trail ON + two_red, a row's stop rides the MID ST
    line (breach ≈ 2nd red); OFF it rides the tightest still-green (fast) line. The
    default (OFF) keeps the validated fast trail unchanged."""
    candles = _candles(_fresh_long_path())
    item = UniverseItem(name="NIFTY 50", tradingsymbol="NIFTY", token=1,
                        exchange="INDICES", option_exchange="NFO", is_index=True)
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    cl = np.array([c.close for c in candles], float)

    cfg_off = SterlingKiteEngineConfig(exit_mode="two_red", exit_aligned_trail=False)
    cfg_on = SterlingKiteEngineConfig(exit_mode="two_red", exit_aligned_trail=True)
    r = compute_regime(o, h, l, cl, cfg_on)
    last = len(cl) - 1

    rows_off = evaluate_item(SterlingKiteEngine(cfg_off), item, candles, cfg_off)
    rows_on = evaluate_item(SterlingKiteEngine(cfg_on), item, candles, cfg_on)
    assert rows_off and rows_on
    assert rows_off[-1].stop_loss == pytest.approx(float(r.l_fast[last]))   # tightest green
    assert rows_on[-1].stop_loss == pytest.approx(float(r.l_mid[last]))     # exit_mode-th line
    assert rows_on[-1].stop_loss != rows_off[-1].stop_loss


def _fresh_long_path():
    return list(np.linspace(300, 150, 60)) + list(np.linspace(150, 600, 80))


def _fresh_short_path():
    return list(np.linspace(150, 600, 60)) + list(np.linspace(600, 150, 80))


def _trim_to_transition(candles, cfg, side="long"):
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c, cfg)
    longs, shorts = entry_transitions(r)
    flags = longs if side == "long" else shorts
    idx = int(np.where(flags)[0][0])
    return candles[: idx + 1]


def test_drop_forming_removes_open_bar():
    candles = _candles([1, 2, 3])  # last ts = 2h
    # "now" only 30 min past the last bar's open → bar still forming
    assert len(drop_forming(candles, now_ms=2 * 3_600_000 + 1_800_000)) == 2
    # well past close → keep it
    assert len(drop_forming(candles, now_ms=10 * 3_600_000)) == 3


def test_drop_forming_preserves_bar_when_allow_forming():
    candles = _candles([1, 2, 3])  # last ts = 2h
    # Even if now_ms is right in the middle of forming bar, allow_forming=True preserves it
    assert len(drop_forming(candles, now_ms=2 * 3_600_000 + 1_800_000, allow_forming=True)) == 3



def _sig_row(ts_ms: int, *, active: bool, fresh: bool):
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow
    return EngineSignalRow(
        underlying="NIFTY", token=1, exchange="NFO",
        regime="BULL", alignment=AlignmentChip(fast=1, mid=1, slow=1),
        direction="long", option_type="CE", spot=100.0, stop_loss=90.0,
        score=85.0, timestamp_ms=ts_ms, is_active=active, is_fresh=fresh,
    )


def test_retain_signals_keeps_active_and_fresh():
    now = 1_000 * 3_600_000
    rows = [_sig_row(now, active=True, fresh=True), _sig_row(now - 3_600_000, active=True, fresh=False)]
    assert _retain_signals(rows, now) == rows


def test_retain_signals_keeps_most_recent_ended_within_window():
    now = 1_000 * 3_600_000
    recent_ended = _sig_row(now - 24 * 3_600_000, active=False, fresh=False)        # ~1 day ago
    older_ended = _sig_row(now - 48 * 3_600_000, active=False, fresh=False)         # ~2 days ago (superseded)
    kept = _retain_signals([older_ended, recent_ended], now)
    # Only the single most-recent ended transition survives; the older one is dropped.
    assert kept == [recent_ended]


def test_retain_signals_drops_ended_past_retention_window():
    now = 1_000 * 3_600_000
    stale = _sig_row(now - _SIGNAL_RETENTION_MS - 3_600_000, active=False, fresh=False)
    assert _retain_signals([stale], now) == []


def test_retain_signals_active_kept_regardless_of_age():
    now = 1_000 * 3_600_000
    old_active = _sig_row(now - _SIGNAL_RETENTION_MS - 10 * 3_600_000, active=True, fresh=False)
    assert _retain_signals([old_active], now) == [old_active]


def test_evaluate_item_emits_row_on_fresh_transition():
    cfg = SterlingKiteEngineConfig()
    eng = SterlingKiteEngine(cfg)
    item = UniverseItem("RELIANCE", "RELIANCE", 111, "NSE", "NFO")
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg)
    rows = evaluate_item(eng, item, candles, cfg)
    assert rows  # all transitions in the window; the latest bar is the fresh long
    row = rows[-1]
    assert row.regime == "BULL" and row.option_type == "CE" and row.direction == "long"
    assert row.token == 111 and row.exchange == "NFO" and row.stop_loss > 0


def test_attach_strike_uses_option_name_for_indices():
    cfg = SterlingKiteEngineConfig()
    eng = SterlingKiteEngine(cfg)
    nifty = UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg)
    rows = evaluate_item(eng, nifty, candles, cfg)
    assert rows
    row = rows[-1]
    spot = row.spot
    base = int(round(spot / 50) * 50)
    dump = [
        {"name": "NIFTY", "tradingsymbol": f"NIFTY25JUN{base}CE", "instrument_type": "CE",
         "strike": base, "expiry": "2026-06-26", "instrument_token": 9001},
        {"name": "NIFTY", "tradingsymbol": f"NIFTY25JUN{base}PE", "instrument_type": "PE",
         "strike": base, "expiry": "2026-06-26", "instrument_token": 9002},
    ]
    attach_strikes(row, dump, option_name="NIFTY", moneynesses=["ATM"], today=date(2026, 6, 13))
    assert len(row.legs) == 1
    assert row.legs[0].option_symbol == f"NIFTY25JUN{base}CE" and row.legs[0].strike == base
    assert row.legs[0].token == 9001


def test_attach_strikes_multi_moneyness_legs():
    cfg = SterlingKiteEngineConfig()
    eng = SterlingKiteEngine(cfg)
    item = UniverseItem("ACME", "ACME", 1, "NSE", "NFO")
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg)
    rows = evaluate_item(eng, item, candles, cfg)
    assert rows
    row = rows[-1]
    spot = row.spot
    base = int(round(spot / 50) * 50)
    dump = [
        {"name": "ACME", "tradingsymbol": f"ACME{base}CE", "instrument_type": "CE",
         "strike": base, "expiry": "2099-01-01", "lot_size": 50},
        {"name": "ACME", "tradingsymbol": f"ACME{base-50}CE", "instrument_type": "CE",
         "strike": base - 50, "expiry": "2099-01-01", "lot_size": 50},
        {"name": "ACME", "tradingsymbol": f"ACME{base-100}CE", "instrument_type": "CE",
         "strike": base - 100, "expiry": "2099-01-01", "lot_size": 50},
    ]
    attach_strikes(row, dump, option_name="ACME", moneynesses=["ATM", "ITM1", "ITM2"],
                   today=date(2026, 6, 13))
    assert [leg.moneyness for leg in row.legs] == ["ATM", "ITM1", "ITM2"]
    assert [leg.strike for leg in row.legs] == [base, base - 50, base - 100]
    assert all(leg.is_active is row.is_active for leg in row.legs)


def test_attach_strikes_otm_legs_and_canonical_order():
    """ITM + ATM + OTM together, resolved in a fixed canonical order (ATM first)
    regardless of the order they were requested in (the UI can scramble it)."""
    cfg = SterlingKiteEngineConfig()
    eng = SterlingKiteEngine(cfg)
    item = UniverseItem("ACME", "ACME", 1, "NSE", "NFO")
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg)
    rows = evaluate_item(eng, item, candles, cfg)
    assert rows
    row = rows[-1]
    spot = row.spot
    base = int(round(spot / 50) * 50)
    dump = [
        {"name": "ACME", "tradingsymbol": f"ACME{s}CE", "instrument_type": "CE",
         "strike": s, "expiry": "2099-01-01", "lot_size": 50}
        for s in (base - 100, base - 50, base, base + 50, base + 100)
    ]
    # requested deliberately out of order
    attach_strikes(row, dump, option_name="ACME",
                   moneynesses=["OTM2", "ATM", "ITM2", "OTM1", "ITM1"], today=date(2026, 6, 13))
    assert [leg.moneyness for leg in row.legs] == ["ATM", "ITM1", "ITM2", "OTM1", "OTM2"]
    # CALL: ITM steps below spot, OTM steps above
    assert [leg.strike for leg in row.legs] == [base, base - 50, base - 100, base + 50, base + 100]


def test_option_order_args_auto_exec_picks_nearest_to_spot_leg():
    """Auto-exec must buy the at-the-money (nearest-spot) contract, never a deep
    OTM lottery — even if an OTM leg happens to be first in the list."""
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[
            OptionLeg(moneyness="OTM2", option_type="CE", option_symbol="N22200CE",
                      strike=22200.0, expiry="2026-06-26", lot_size=75),
            OptionLeg(moneyness="ATM", option_type="CE", option_symbol="N22000CE",
                      strike=22000.0, expiry="2026-06-26", lot_size=75),
            OptionLeg(moneyness="ITM1", option_type="CE", option_symbol="N21900CE",
                      strike=21900.0, expiry="2026-06-26", lot_size=75),
        ],
        spot=22010.0, stop_loss=21900.0, score=85.0, timestamp_ms=123,
    )
    assert option_order_args(row)["option_symbol"] == "N22000CE"  # nearest to spot 22010


@pytest.mark.asyncio
async def test_scan_end_to_end_with_fake_client():
    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            # one item fires (fresh transition), the other is flat noise
            if inst.zerodha_token == 1:
                return _trim_to_transition(_candles(_fresh_long_path()),
                                           SterlingKiteEngineConfig())
            return _candles(list(np.linspace(100, 101, 30)))

    base = 400  # rough ATM for the fired item (rises to ~600 region trimmed earlier)
    nfo = [
        {"name": "ACME", "tradingsymbol": "ACME25JUN300CE", "instrument_type": "CE",
         "strike": 300, "expiry": "2099-01-01"},
        {"name": "ACME", "tradingsymbol": "ACME25JUN300PE", "instrument_type": "PE",
         "strike": 300, "expiry": "2099-01-01"},
    ]
    universe = [
        UniverseItem("ACME", "ACME", 1, "NSE", "NFO"),
        UniverseItem("DULL", "DULL", 2, "NSE", "NFO"),
    ]
    sc = KiteEngineScanner()
    # candles are far in the past, so drop_forming keeps the last bar
    await sc.scan(uid="u1", client=FakeClient(), universe=universe, nfo_rows=nfo,
                  bfo_rows=[], cfg=SterlingKiteEngineConfig(), moneyness=["ATM"])
    snap = sc.snapshot("u1")
    assert not snap.scanning and snap.generated_ms > 0
    # only the firing item appears (DULL is flat); evaluate_item now returns all
    # transitions in the window, so there may be >1 ACME row (e.g. a short then the long)
    assert {r.underlying for r in snap.rows} == {"ACME"}
    fresh = max(snap.rows, key=lambda r: r.timestamp_ms)  # latest bar = the fresh long/CE
    assert fresh.option_type == "CE" and fresh.legs[0].option_symbol == "ACME25JUN300CE"


def test_evaluate_derivative_contract_emits_buy_on_premium_uptrend():
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)
    pick = OptionPick(option_symbol="NIFTY25JUN24500CE", strike=24500.0, option_type="CE",
                      expiry="2026-06-26", dte=8, lot_size=75, token=44001)
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")  # premium uptrend
    rows = evaluate_derivative_contract(item, "ATM", pick, candles, cfg)
    assert rows
    row = rows[-1]  # latest-bar BUY entry
    assert row.source == "derivatives"
    assert row.option_type == "CE" and row.regime == "BULL" and row.direction == "long"
    assert row.token == 44001  # the option's OWN token (click → option premium chart)
    assert row.legs and row.legs[0].option_symbol == "NIFTY25JUN24500CE"
    assert row.legs[0].moneyness == "ATM" and row.legs[0].lot_size == 75
    assert row.spot == candles[-1].close  # headline price = option premium last close
    assert row.stop_loss > 0  # premium-based SuperTrend trail


def test_evaluate_derivative_contract_pe_is_bearish():
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("BANKNIFTY", "BANKNIFTY", 260105, "INDICES", "NFO", is_index=True)
    pick = OptionPick(option_symbol="BANKNIFTY25JUN54000PE", strike=54000.0, option_type="PE",
                      expiry="2026-06-26", dte=8, lot_size=15, token=55001)
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")  # PE premium rising
    rows = evaluate_derivative_contract(item, "ATM", pick, candles, cfg)
    assert rows
    row = rows[-1]
    assert row.option_type == "PE" and row.regime == "BEAR" and row.direction == "long"


def test_evaluate_derivative_contract_buy_only_skips_premium_downtrend():
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)
    pick = OptionPick(option_symbol="NIFTY25JUN24500CE", strike=24500.0, option_type="CE",
                      expiry="2026-06-26", dte=8, lot_size=75, token=44001)
    # a premium that ONLY falls has no uptrend transition → no BUY ever (buy-only)
    candles = _candles(list(np.linspace(600, 150, 140)))
    assert evaluate_derivative_contract(item, "ATM", pick, candles, cfg) == []


def test_evaluate_derivative_contract_old_entry_dead_after_stop_breach():
    """An entry whose trail later flipped down (premium crashed through the stop) is
    NOT 'running', even if the premium recovered into a fresh trail-up afterwards.

    Regression: is_active used the latest-bar trail for EVERY historical entry, so a
    long-dead entry (e.g. bought at 971, premium since collapsed to 200) was shown as
    a live 'running' signal whenever the trail bounced back up.
    """
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)
    pick = OptionPick(option_symbol="NIFTY25JUN24500CE", strike=24500.0, option_type="CE",
                      expiry="2026-06-26", dte=8, lot_size=75, token=44001)
    # rise → fresh entry; crash (trail flips down → entry dead); recover (trail flips up again)
    path = (list(np.linspace(300, 150, 60)) + list(np.linspace(150, 600, 80))
            + list(np.linspace(600, 120, 50)) + list(np.linspace(120, 500, 70)))


def test_evaluate_item_is_active_respects_exit_mode():
    """Different exit_mode changes when a historical entry becomes !is_active."""
    item = UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)
    # simple rise then crash path
    path = list(np.linspace(300, 150, 30)) + list(np.linspace(150, 600, 40)) + list(np.linspace(600, 200, 30))
    candles = _candles(path)

    # loose mode (three_red)
    cfg_loose = SterlingKiteEngineConfig(exit_mode="three_red")
    eng_loose = SterlingKiteEngine(cfg_loose)
    rows_loose = evaluate_item(eng_loose, item, candles, cfg_loose)
    active_loose = any(r.is_active for r in rows_loose)

    # tight mode (one_red)
    cfg_tight = SterlingKiteEngineConfig(exit_mode="one_red")
    eng_tight = SterlingKiteEngine(cfg_tight)
    rows_tight = evaluate_item(eng_tight, item, candles, cfg_tight)
    active_tight = any(r.is_active for r in rows_tight)

    # logic ran; bools
    assert isinstance(active_loose, bool) and isinstance(active_tight, bool)


def test_engine_config_default_offers_itm_and_otm():
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    cfg = EngineConfigModel()
    assert cfg.strike_moneyness == ["ITM1", "ATM", "OTM1"]


def test_engine_config_scan_source_and_universe_defaults():
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    c = EngineConfigModel()
    assert c.scan_source == "spot"          # validated default source (was "derivatives")
    assert c.scan_all_stocks is False       # indices/curated only by default
    assert "NIFTY 50" in c.scan_indices and len(c.scan_indices) == 4
    c2 = EngineConfigModel(scan_source="both", scan_all_stocks=False,
                           scan_indices=["NIFTY 50"], scan_stocks=["RELIANCE"])
    assert c2.scan_indices == ["NIFTY 50"] and c2.scan_stocks == ["RELIANCE"]


def test_engine_config_accepts_otm_only_selection():
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    cfg = EngineConfigModel(strike_moneyness=["OTM1", "OTM2"])
    assert cfg.strike_moneyness == ["OTM1", "OTM2"]
    # empty falls back to the default set (validator)
    assert EngineConfigModel(strike_moneyness=[]).strike_moneyness == ["ITM1", "ATM", "OTM1"]


def test_option_order_args_maps_buy_one_lot():
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="NIFTY25JUN22000CE",
                        strike=22000.0, expiry="2026-06-26", lot_size=75)],
        spot=22010.0, stop_loss=21900.0, score=85.0, timestamp_ms=123,
    )
    args = option_order_args(row)  # primary leg
    assert args == {
        "option_symbol": "NIFTY25JUN22000CE", "side": "buy", "size": 75,
        "lot_size": 75, "token": 0, "exchange": "NFO",
        # Every price here is in the PREMIUM domain. This spot row's stop_loss is an
        # index level (21,900), so it is NOT reported as the option's stop — the order
        # path resolves a real premium stop from a live quote instead.
        "stop_loss": None,
        # premium basis for risk sizing — None here (no premium_spot/premium_sl on the leg)
        "entry_premium": None, "stop_premium": None,
    }
    # a put (bear) is still a BUY — this is an options-buying engine
    row.direction = "short"; row.option_type = "PE"
    assert option_order_args(row)["side"] == "buy"
    # no legs → no order
    row.legs = []
    assert option_order_args(row) is None


def test_spot_premium_snapshot_reuses_entry_and_recomputes_tsl():
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    old = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="NIFTY26JUN25000CE",
                        strike=25000.0, expiry="2099-01-01", lot_size=75,
                        premium_spot=100.0, premium_sl=1.0, entry_sl=80.0)],
        spot=25000.0, stop_loss=24800.0, score=85.0, timestamp_ms=123,
    )
    new = old.model_copy(deep=True)
    new.stop_loss = 24950.0
    new.legs = [new.legs[0].model_copy(update={"premium_spot": None, "premium_sl": None, "entry_sl": None})]

    _copy_prior_leg_snapshot(new, _prior_leg_snapshots([old]))
    assert new.legs[0].premium_spot == pytest.approx(100.0)
    assert new.legs[0].entry_sl == pytest.approx(80.0)

    _stamp_leg_premium_stops(new, new.legs[0])
    assert new.legs[0].premium_sl is not None
    assert new.legs[0].premium_sl != pytest.approx(1.0)
    assert new.legs[0].entry_sl == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_scan_spot_hydrates_entry_sl_and_tsl_premium_snapshots():
    cfg = SterlingKiteEngineConfig()
    fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")
    entry_ts = fired[-1].timestamp_ms
    premium = _candles(list(np.linspace(80, 123, len(fired))))

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return fired
            if inst.zerodha_token == 7001:
                return premium
            return []

    base = int(round(fired[-1].close / 50) * 50)
    nfo = [
        {"name": "ACME", "tradingsymbol": f"ACME25JUN{base}CE", "instrument_type": "CE",
         "strike": base, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 50},
    ]
    sc = KiteEngineScanner()
    await sc.scan(
        uid="spot-premium-user",
        client=FakeClient(),
        universe=[UniverseItem("ACME", "ACME", 100, "NSE", "NFO")],
        nfo_rows=nfo,
        bfo_rows=[],
        cfg=cfg,
        moneyness=["ATM"],
    )

    rows = [row for row in sc.snapshot("spot-premium-user").rows if row.source == "spot"]
    assert rows
    row = max(rows, key=lambda value: value.timestamp_ms)
    assert row.timestamp_ms == entry_ts
    leg = row.legs[0]
    assert leg.token == 7001
    assert leg.premium_spot == pytest.approx(premium[-1].close)
    assert leg.entry_sl is not None and leg.entry_sl > 0
    assert leg.premium_sl is not None and leg.premium_sl > 0


@pytest.mark.asyncio
async def test_scan_records_index_diagnostics():
    """The scan exposes a per-run breakdown so a silently-empty index candle fetch
    is visible (this is the 'no index signals' diagnostic)."""
    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token in (1, 10):  # a stock + an index fire
                return _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig())
            if inst.zerodha_token == 11:        # an index whose fetch comes back empty
                return []
            return _candles(list(np.linspace(100, 101, 30)))  # token 2: data, no transition

    universe = [
        UniverseItem("ACME", "ACME", 1, "NSE", "NFO"),
        UniverseItem("DULL", "DULL", 2, "NSE", "NFO"),
        UniverseItem("NIFTY 50", "NIFTY", 10, "INDICES", "NFO", is_index=True),
        UniverseItem("SENSEX", "SENSEX", 11, "INDICES", "BFO", is_index=True),
    ]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=universe, nfo_rows=[],
                  bfo_rows=[], cfg=SterlingKiteEngineConfig(), moneyness=["ATM"])
    d = sc.snapshot("u1").diag
    assert d.universe == 4 and d.indices == 2
    assert d.evaluated == 3 and d.no_data == 1      # token 11 returned nothing
    assert d.index_evaluated == 1 and d.index_no_data == 1 and d.index_fired == 1


@pytest.mark.asyncio
async def test_scan_derivatives_charts_both_sides_and_emits_buy_rows():
    """Derivatives mode charts BOTH the CE and PE of each selected strike (on the
    contract's own premium series) and emits a BUY row only when the premium fires."""
    fired = _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig(), "long")
    flat = _candles(list(np.linspace(100, 101, 40)))

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            t = inst.zerodha_token
            if t == 100:    # underlying spot anchor → last close 100 (no transition needed)
                return _candles([100.0] * 40)
            if t == 7001:   # ATM CE premium → fresh uptrend → BUY
                return fired
            return flat     # 7002 ATM PE → has data, no transition → no signal

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100PE", "instrument_type": "PE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(), moneyness=["ATM"], deriv_universe=deriv)
    snap = sc.snapshot("u1")
    assert len(snap.rows) == 1
    row = snap.rows[0]
    assert row.source == "derivatives" and row.option_type == "CE"
    assert row.token == 7001 and row.legs[0].option_symbol == "NIFTY25JUN100CE"
    d = snap.diag
    assert d.deriv_charts == 2 and d.deriv_fired == 1 and d.deriv_no_data == 0
    assert d.deriv_resolved == 2            # both contracts resolved from the chain
    assert d.deriv_max_bars >= d.deriv_min_bars > 0  # premium history depth recorded


@pytest.mark.asyncio
async def test_scan_derivatives_does_not_skip_short_weeklies():
    """A short-dated contract is still CHARTED (not skipped); it just can't fire a
    21-period SuperTrend yet, so it produces no signal — never a fabricated one."""
    short = _candles([100.0] * 12)  # 12 bars < warmup(21): scanned, but no signal possible

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return _candles([100.0] * 40)  # underlying spot anchor
            return short  # both CE & PE are young weeklies

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100PE", "instrument_type": "PE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(), moneyness=["ATM"], deriv_universe=deriv)
    snap = sc.snapshot("u1")
    d = snap.diag
    assert d.deriv_charts == 2      # both young weeklies were charted (not skipped)
    assert d.deriv_no_data == 0     # present-but-short ≠ no-data
    assert d.deriv_fired == 0 and snap.rows == []  # no fabricated signal


@pytest.mark.asyncio
async def test_scan_derivatives_invokes_place_cb_for_auto_exec():
    """Auto-exec is universal: a fired derivative contract goes through place_cb too."""
    fired = _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig(), "long")

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return _candles([100.0] * 40)
            if inst.zerodha_token == 7001:
                return fired
            return _candles(list(np.linspace(100, 101, 40)))

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100PE", "instrument_type": "PE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    calls = []

    async def cb(row, item):
        calls.append((row.source, row.legs[0].option_symbol, row.legs[0].lot_size))

    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(), moneyness=["ATM"], deriv_universe=deriv, place_cb=cb)
    assert calls == [("derivatives", "NIFTY25JUN100CE", 75)]


class TestContractStaleness:
    """"Fired on its own last bar" is not "fired now"."""

    HOUR = 3_600_000

    def test_a_contract_level_with_its_underlying_is_current(self):
        assert contract_bar_is_current(10 * self.HOUR, 10 * self.HOUR)

    def test_a_contract_ahead_of_its_underlying_is_current(self):
        # The contract's series can legitimately extend past the underlying's in a
        # fixture or a partial fetch; only LAG is a staleness signal.
        assert contract_bar_is_current(12 * self.HOUR, 10 * self.HOUR)

    def test_a_lagging_contract_is_not_current(self):
        assert not contract_bar_is_current(7 * self.HOUR, 10 * self.HOUR)

    def test_the_allowance_admits_exactly_that_many_bars(self):
        assert contract_bar_is_current(9 * self.HOUR, 10 * self.HOUR, max_stale_bars=1)
        assert not contract_bar_is_current(8 * self.HOUR, 10 * self.HOUR, max_stale_bars=1)

    def test_an_unknown_underlying_clock_does_not_block(self):
        """The underlying candle fetch can fail while a quote-derived spot carries the
        scan. Refusing every automatic entry then is a bigger action than this guard
        exists to take."""
        assert contract_bar_is_current(7 * self.HOUR, 0)


def _staleness_harness(fired, under_last_bar_h):
    """A NIFTY chain where the underlying's last 1H bar lands at ``under_last_bar_h``."""
    under_start = (under_last_bar_h - 39) * 3_600_000

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return _candles([100.0] * 40, start_ms=under_start)
            if inst.zerodha_token == 7001:
                return fired
            return _candles(list(np.linspace(100, 101, 40)), start_ms=under_start)

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100PE", "instrument_type": "PE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    return FakeClient(), nfo, deriv


@pytest.mark.asyncio
async def test_a_stale_contract_is_shown_but_not_auto_executed():
    """The illiquid-strike case: the signal is real, the bar it fired on is hours old.

    A market order here fills nowhere near the premium the row is quoting. The row
    still belongs on the board — the user may want to see it — so only the automatic
    order is withheld.
    """
    fired = _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig(), "long")
    contract_last_h = fired[-1].timestamp_ms // 3_600_000
    client, nfo, deriv = _staleness_harness(fired, contract_last_h + 3)  # 3 bars behind
    calls = []

    async def cb(row, item):
        calls.append(row.legs[0].option_symbol)

    sc = KiteEngineScanner()
    await sc.scan(uid="stale1", client=client, universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(), moneyness=["ATM"],
                  deriv_universe=deriv, place_cb=cb)
    snap = sc.snapshot("stale1")
    assert calls == [], "a 3-bar-stale contract must not auto-execute"
    assert snap.diag.deriv_fired == 1, "it still counts as fired"
    assert snap.diag.deriv_stale_skipped == 1
    assert any(leg.option_symbol == "NIFTY25JUN100CE"
               for row in snap.rows for leg in row.legs), "and it still shows on the board"


@pytest.mark.asyncio
async def test_the_staleness_allowance_lets_a_thin_strike_through():
    """Raising the allowance is how someone opts into trading thinner strikes."""
    fired = _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig(), "long")
    contract_last_h = fired[-1].timestamp_ms // 3_600_000
    client, nfo, deriv = _staleness_harness(fired, contract_last_h + 3)
    calls = []

    async def cb(row, item):
        calls.append(row.legs[0].option_symbol)

    sc = KiteEngineScanner()
    await sc.scan(uid="stale2", client=client, universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(max_contract_staleness_bars=3),
                  moneyness=["ATM"], deriv_universe=deriv, place_cb=cb)
    assert calls == ["NIFTY25JUN100CE"]
    assert sc.snapshot("stale2").diag.deriv_stale_skipped == 0


@pytest.mark.asyncio
async def test_deriv_index_spot_fallback_quotes_by_display_name():
    """When an index's 1H candle fetch comes back empty, the deriv scan resolves the
    spot from a QUOTE keyed by the DISPLAY name ("NSE:NIFTY 50"), not the option name
    ("NSE:NIFTY") which is not a valid quote symbol. Proves the chain still scans."""
    fired = _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig(), "long")
    quoted = {}

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 256265:  # the index underlying → EMPTY (silent drop)
                return []
            if inst.zerodha_token == 7001:     # ATM CE premium → fires
                return fired
            return _candles(list(np.linspace(100, 101, 40)))

        async def get_quote(self, syms):
            quoted['syms'] = list(syms)
            # Only the DISPLAY-name symbol resolves; the option-name symbol would 404.
            return {"NSE:NIFTY 50": {"last_price": 24510.0}}

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN24500CE", "instrument_type": "CE",
         "strike": 24500, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN24500PE", "instrument_type": "PE",
         "strike": 24500, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(), moneyness=["ATM"], deriv_universe=deriv)
    assert quoted['syms'] == ["NSE:NIFTY 50"]          # display name, not "NSE:NIFTY"
    snap = sc.snapshot("u1")
    assert snap.diag.deriv_no_spot == 0                # spot WAS resolved via the quote
    assert len(snap.rows) == 1 and snap.rows[0].option_type == "CE"


@pytest.mark.asyncio
async def test_deriv_unresolved_spot_is_visible_not_silent():
    """If BOTH the candle fetch and the quote come back empty, the underlying is
    skipped — but it's COUNTED (deriv_no_spot), never a silent drop."""
    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            return []  # everything empty

        async def get_quote(self, syms):
            return {}  # quote also empty

    deriv = [UniverseItem("SENSEX", "SENSEX", 265, "INDICES", "BFO", is_index=True)]
    logs = []
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=[], bfo_rows=[],
                  cfg=SterlingKiteEngineConfig(), moneyness=["ATM"], deriv_universe=deriv,
                  log_cb=lambda m: logs.append(m))
    d = sc.snapshot("u1").diag
    assert d.deriv_no_spot == 1 and d.deriv_resolved == 0
    assert any("spot unavailable" in m for m in logs)


def test_derivative_signal_marks_active_when_trend_intact_vs_stale():
    """A signal whose premium SuperTrend is still aligned on the latest bar is
    is_active=True (running); once the premium reverses and the trend breaks it goes
    is_active=False (stale entry, kept only for history). This is the fix for "I see a
    big move on the chart but the engine shows nothing today" — the entry was days ago
    and the trend has since ended."""
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("SENSEX", "SENSEX", 265, "INDICES", "BFO", is_index=True)
    pick = OptionPick(option_symbol="SENSEX2561876000CE", strike=76000.0, option_type="CE",
                      expiry="2026-06-18", dte=3, lot_size=20, token=999001)

    # still rising on the last bar → active
    rising = _candles(_fresh_long_path())
    rows = evaluate_derivative_contract(item, "ATM", pick, rising, cfg)
    assert rows and rows[-1].is_active is True and rows[-1].legs[0].is_active is True

    # entered (proven firing path), then reversed hard so the trend breaks before the
    # last bar → the entry still exists but is no longer running
    reversed_ = _candles(_fresh_long_path() + list(np.linspace(600, 150, 40)))
    rows2 = evaluate_derivative_contract(item, "ATM", pick, reversed_, cfg)
    assert rows2 and rows2[0].is_active is False
    assert rows2[0].legs[0].is_active is False


@pytest.mark.asyncio
async def test_deriv_grouping_dedupes_legs_for_repeated_transitions():
    """A contract that fires more than once over its premium history yields ONE leg
    (the most recent), not a duplicate strike chip per transition."""
    cfg = SterlingKiteEngineConfig()
    # premium that transitions up TWICE (up, down, up again) → 2 long transitions
    twice = (list(np.linspace(300, 150, 50)) + list(np.linspace(150, 600, 40))
             + list(np.linspace(600, 200, 40)) + list(np.linspace(200, 700, 40)))

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return _candles([100.0] * 40)       # underlying anchor
            if inst.zerodha_token == 7001:
                return _candles(twice)               # ATM CE fires twice
            return _candles(list(np.linspace(100, 101, 40)))  # PE flat

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100PE", "instrument_type": "PE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], deriv_universe=deriv)
    rows = sc.snapshot("u1").rows
    assert len(rows) == 1
    syms = [l.option_symbol for l in rows[0].legs]
    assert syms == ["NIFTY25JUN100CE"]              # ONE leg, not duplicated per transition


@pytest.mark.asyncio
async def test_scan_drops_stopped_out_historical_entries_without_report_payload():
    """Dead historical entries stay off the board without retaining an obsolete
    per-contract report payload. Symbol-only de-duplication remains internal."""
    cfg = SterlingKiteEngineConfig()
    # fired (proven long path) then collapsed hard → trend broke before the last bar:
    # an entry exists in history but is_active=False and not fresh.
    dead = _candles(_fresh_long_path() + list(np.linspace(600, 150, 40)))

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return _candles([100.0] * 40)                     # underlying anchor
            if inst.zerodha_token == 7001:
                return dead                                       # ATM CE: dead historical entry
            return _candles(list(np.linspace(100, 101, 40)))      # PE flat → no transition

    nfo = [
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75},
        {"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100PE", "instrument_type": "PE",
         "strike": 100, "expiry": "2099-01-01", "instrument_token": 7002, "lot_size": 75},
    ]
    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], deriv_universe=deriv)
    snap = sc.snapshot("u1")
    assert snap.rows == []  # dead historical entry not surfaced
    assert not hasattr(snap.diag, "contracts")
    assert snap.scanned_contract_symbols == {
        "NIFTY25JUN100CE",
        "NIFTY25JUN100PE",
    }


@pytest.mark.asyncio
async def test_scan_invokes_place_cb_for_ready_rows():
    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 1:
                return _trim_to_transition(_candles(_fresh_long_path()), SterlingKiteEngineConfig())
            return _candles(list(np.linspace(100, 101, 30)))

    nfo = [
        {"name": "ACME", "tradingsymbol": "ACME25JUN300CE", "instrument_type": "CE",
         "strike": 300, "expiry": "2099-01-01", "lot_size": 50},
        {"name": "ACME", "tradingsymbol": "ACME25JUN300PE", "instrument_type": "PE",
         "strike": 300, "expiry": "2099-01-01", "lot_size": 50},
    ]
    universe = [
        UniverseItem("ACME", "ACME", 1, "NSE", "NFO"),
        UniverseItem("DULL", "DULL", 2, "NSE", "NFO"),
    ]
    calls = []

    async def cb(row, item):
        leg = row.legs[0]
        calls.append((row.underlying, leg.option_symbol, leg.lot_size))

    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=universe, nfo_rows=nfo,
                  bfo_rows=[], cfg=SterlingKiteEngineConfig(), moneyness=["ATM"], place_cb=cb)
    assert calls == [("ACME", "ACME25JUN300CE", 50)]


# ── UserScan.row_for_token (O(1) index used by the detail endpoint) ───────────
def _row(token, ts, legs=()):
    from app.engines.sterling_kite_engine.schemas import EngineSignalRow, AlignmentChip, OptionLeg
    return EngineSignalRow(
        underlying="NIFTY", token=token, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long",
        option_type="CE", spot=100.0, stop_loss=95.0, score=85.0, timestamp_ms=ts,
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol=s,
                        strike=k, expiry="2026-06-25", token=lt)
              for (s, k, lt) in legs],
    )


def test_row_for_token_matches_own_and_leg_tokens():
    from app.services.kite_engine.scanner import UserScan
    from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    us = UserScan(engine=SterlingKiteEngine(SterlingKiteEngineConfig()))
    us.rows = [_row(111, 1000, legs=[("NIFTYCE", 25000, 5001), ("NIFTYCE2", 25100, 5002)]),
               _row(222, 1000, legs=[("BANKCE", 50000, 6001)])]
    us.generated_ms = 1000
    assert us.row_for_token(111).token == 111          # own token
    assert us.row_for_token(5002).token == 111         # leg token → parent row
    assert us.row_for_token(6001).token == 222
    assert us.row_for_token(999) is None               # unknown


def test_row_for_token_reindexes_after_new_scan():
    from app.services.kite_engine.scanner import UserScan
    from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    us = UserScan(engine=SterlingKiteEngine(SterlingKiteEngineConfig()))
    us.rows = [_row(111, 1000)]
    us.generated_ms = 1000
    assert us.row_for_token(111).token == 111
    # new scan lands with different rows + generated_ms → index must rebuild
    us.rows = [_row(333, 2000)]
    us.generated_ms = 2000
    assert us.row_for_token(111) is None
    assert us.row_for_token(333).token == 333


def test_row_for_token_respects_timestamp():
    from app.services.kite_engine.scanner import UserScan
    from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    us = UserScan(engine=SterlingKiteEngine(SterlingKiteEngineConfig()))
    us.rows = [_row(111, 1000)]
    us.generated_ms = 1000
    assert us.row_for_token(111, timestamp_ms=1000).token == 111
    assert us.row_for_token(111, timestamp_ms=9999) is None   # ts mismatch


# ── New per-signal fields: entry_sl (initial stop) + exit_state (red counter) ──
def test_evaluate_item_populates_entry_sl_and_exit_state():
    """Every emitted underlying row carries the SL column (initial stop at the entry
    bar = the validated fast ST line) and the Exit column (red-counter progress
    "<reds>/<threshold> red" per exit_mode)."""
    cfg = SterlingKiteEngineConfig()  # trail_target=fast, exit_mode=one_red
    eng = SterlingKiteEngine(cfg)
    item = UniverseItem("ACME", "ACME", 1, "NSE", "NFO")
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg)
    rows = evaluate_item(eng, item, candles, cfg)
    assert rows
    row = rows[-1]  # the fresh long
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    cl = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, cl, cfg)
    longs, _ = entry_transitions(r)
    i = int(np.where(longs)[0][-1])
    assert row.entry_sl == pytest.approx(float(r.l_fast[i]))  # initial stop = fast line at entry
    # fresh long ⇒ 0 reds against under one_red (threshold 1)
    assert row.exit_state == "0/1 red"


def test_evaluate_derivative_contract_populates_entry_sl_and_exit_state():
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("NIFTY 50", "NIFTY", 256265, "INDICES", "NFO", is_index=True)
    pick = OptionPick(option_symbol="NIFTY25JUN24500CE", strike=24500.0, option_type="CE",
                      expiry="2026-06-26", dte=8, lot_size=75, token=44001)
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")
    rows = evaluate_derivative_contract(item, "ATM", pick, candles, cfg)
    assert rows
    row = rows[-1]
    assert row.entry_sl is not None and row.entry_sl > 0
    assert row.legs[0].entry_sl is not None and row.legs[0].entry_sl > 0
    assert row.exit_state and row.exit_state.endswith("red")


def test_engine_config_accepts_confluence_source():
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    c = EngineConfigModel(scan_source="confluence")
    assert c.scan_source == "confluence"


def _wide_ce_pe_chain(base: int):
    """A CE+PE chain spanning ±200 around ``base`` so pick_strikes finds an ATM with a
    real instrument_token whatever the exact spot turns out to be."""
    nfo, tok = [], 7000
    for s in range(base - 200, base + 201, 50):
        tok += 1
        nfo.append({"name": "ACME", "tradingsymbol": f"ACME25JUN{s}CE", "instrument_type": "CE",
                    "strike": s, "expiry": "2099-01-01", "instrument_token": tok, "lot_size": 50})
        tok += 1
        nfo.append({"name": "ACME", "tradingsymbol": f"ACME25JUN{s}PE", "instrument_type": "PE",
                    "strike": s, "expiry": "2099-01-01", "instrument_token": tok, "lot_size": 50})
    return nfo


class TestTheScanKeepsARedReadingForEveryInstrument:
    """A signal row exists only where there was an entry TRANSITION.

    Positions routinely outlive the row that opened them, and the red-count exit reads
    its number off those rows — so it used to freeze for the rest of the trade. The
    scan computes the regime for every instrument regardless; these pin that the
    reading is kept, on every scan source, INCLUDING when no signal comes out.
    """

    @staticmethod
    def _quiet_client():
        class FakeClient:
            async def get_candles(self, inst, resolution, limit):
                return _candles(list(np.linspace(100, 101, 60)))   # no transition
        return FakeClient()

    @pytest.mark.asyncio
    async def test_a_spot_scan_with_no_signal_still_records_one(self):
        sc = KiteEngineScanner()
        item = UniverseItem("ACME", "ACME", 100, "NSE", "NFO")
        await sc.scan(uid="reds1", client=self._quiet_client(), universe=[item],
                      nfo_rows=[], bfo_rows=[], cfg=SterlingKiteEngineConfig(),
                      moneyness=["ATM"])
        snap = sc.snapshot("reds1")
        assert snap.rows == [], "precondition: this scan emits no signal row"
        assert "ACME" in snap.underlying_reds
        assert set(snap.underlying_reds["ACME"]) == {"long", "short"}

    @pytest.mark.asyncio
    async def test_a_confluence_scan_records_one_too(self):
        """Confluence is a scan_source of its own — the spot pass never runs under it,
        so without its own capture an account on confluence would keep the old freeze."""
        sc = KiteEngineScanner()
        conf = [UniverseItem("ACME", "ACME", 100, "NSE", "NFO")]
        await sc.scan(uid="reds2", client=self._quiet_client(), universe=[],
                      nfo_rows=[], bfo_rows=[], cfg=SterlingKiteEngineConfig(),
                      moneyness=["ATM"], confluence_universe=conf)
        assert "ACME" in sc.snapshot("reds2").underlying_reds

    @pytest.mark.asyncio
    async def test_a_derivatives_scan_records_the_contract_it_charted(self):
        cfg = SterlingKiteEngineConfig()
        fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")

        class FakeClient:
            async def get_candles(self, inst, resolution, limit):
                if inst.zerodha_token == 100:
                    return _candles([100.0] * 60)
                return fired

        nfo = [{"name": "NIFTY", "tradingsymbol": "NIFTY25JUN100CE", "instrument_type": "CE",
                "strike": 100, "expiry": "2099-01-01", "instrument_token": 7001, "lot_size": 75}]
        deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
        sc = KiteEngineScanner()
        await sc.scan(uid="reds3", client=FakeClient(), universe=[], nfo_rows=nfo,
                      bfo_rows=[], cfg=cfg, moneyness=["ATM"], deriv_universe=deriv)
        assert "NIFTY25JUN100CE" in sc.snapshot("reds3").contract_reds

    def test_too_few_bars_is_unknown_rather_than_zero(self):
        """A zero would read as "nothing against us" and disarm the exit."""
        assert live_red_counts(_candles([100.0] * 3), SterlingKiteEngineConfig()) == {}


@pytest.mark.asyncio
async def test_scan_confluence_emits_merged_row_when_both_fire():
    """confluence source: the underlying fires a fresh long AND the chosen option's own
    premium ST also confirms → ONE merged source='confluence' row with the confirmed leg
    carrying its premium entry/stop."""
    cfg = SterlingKiteEngineConfig()
    fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:      # the underlying fires long
                return fired
            return _candles(_fresh_long_path())  # whichever option is picked, its premium confirms

    base = int(round(fired[-1].close / 50) * 50)
    nfo = _wide_ce_pe_chain(base)
    conf = [UniverseItem("ACME", "ACME", 100, "NSE", "NFO")]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], confluence_universe=conf)
    snap = sc.snapshot("u1")
    rows = [r for r in snap.rows if r.source == "confluence"]
    assert rows, "expected a confluence row when both spot and premium fire"
    row = max(rows, key=lambda r: r.timestamp_ms)
    assert row.direction == "long" and row.option_type == "CE"
    assert len(row.legs) == 1 and row.legs[0].moneyness == "ATM"
    assert row.legs[0].premium_spot is not None and row.legs[0].premium_sl is not None
    assert row.legs[0].entry_sl is not None
    assert row.entry_sl is not None and row.exit_state


@pytest.mark.asyncio
async def test_scan_with_empty_spot_universe_does_not_wipe_existing_rows():
    """Regression: a scan_source other than spot/both (confluence here) passes an
    EMPTY ``universe`` to scan() — the spot phase's asyncio.gather over zero items
    must never stomp us.rows with the still-empty local accumulator before the
    confluence/derivatives phases below get a chance to run. A second scan with
    nothing new to find (empty universe AND empty confluence_universe) must leave
    the board exactly as the first scan left it."""
    cfg = SterlingKiteEngineConfig()
    fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return fired
            return _candles(_fresh_long_path())

    base = int(round(fired[-1].close / 50) * 50)
    nfo = _wide_ce_pe_chain(base)
    conf = [UniverseItem("ACME", "ACME", 100, "NSE", "NFO")]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], confluence_universe=conf)
    populated = sc.snapshot("u1").rows
    assert populated, "first scan should have found a confluence row"

    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], confluence_universe=[])
    assert sc.snapshot("u1").rows == populated


@pytest.mark.asyncio
async def test_scan_hydrates_persisted_cache_on_first_touch_after_restart(monkeypatch):
    """Regression: scan() claims the uid slot via _user(), not snapshot() — on a
    fresh process (e.g. right after a backend restart) _user() used to create an
    empty UserScan without loading the DB-persisted signal cache, so the very
    first scan (typically the auto-loop, seconds after the process comes up)
    would run with us.rows=[] for its whole duration. Any /signals poll landing
    in that window saw a blanked board even though good signals were sitting in
    the DB cache, unused, the entire time."""
    from app.services.kite_engine import state

    cfg = SterlingKiteEngineConfig()
    fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return fired
            return _candles(_fresh_long_path())

    base = int(round(fired[-1].close / 50) * 50)
    nfo = _wide_ce_pe_chain(base)
    conf = [UniverseItem("ACME", "ACME", 100, "NSE", "NFO")]

    # Populate a real cache payload once, on a throwaway scanner instance.
    seed_sc = KiteEngineScanner()
    await seed_sc.scan(uid="u2", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                       cfg=cfg, moneyness=["ATM"], confluence_universe=conf)
    persisted_rows = seed_sc.snapshot("u2").rows
    assert persisted_rows

    # A brand-new scanner (empty _users dict) simulates the post-restart state;
    # monkeypatch load_signal_cache so the test doesn't depend on the DB backend
    # actually being available in this environment.
    cached_payload = ([r.model_dump() for r in persisted_rows], 12345)
    monkeypatch.setattr(state, "load_signal_cache", lambda uid: cached_payload if uid == "u2" else None)

    fresh_sc = KiteEngineScanner()
    us = fresh_sc._user("u2", cfg)  # the exact path scan() takes internally
    assert us.rows, "expected _user() to hydrate from the persisted cache like snapshot() does"
    assert [r.underlying for r in us.rows] == [r.underlying for r in persisted_rows]


@pytest.mark.asyncio
async def test_scan_confluence_leg_premium_is_current_not_stale_entry():
    """Regression (scanner.py:826): when the underlying fires fresh but the chosen
    option's premium has been trending for several bars (is_active, NOT is_fresh),
    the confirmed leg's premium_spot must be the CURRENT premium (last closed bar) —
    the price we actually enter at now — not the option's stale ST entry-bar premium.
    Stamping the stale entry-bar value would show a fake unrealized gain and, if the
    WS fill postback is missed, book a wrong realized PnL into the daily-loss breaker."""
    cfg = SterlingKiteEngineConfig()
    fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")
    # Option premium climbs 150→600 through the up-leg; its ST long fires mid-climb and
    # stays running to the last bar, so the entry-bar premium is far below the last close.
    premium = _candles(_fresh_long_path())

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return fired
            return premium

    base = int(round(fired[-1].close / 50) * 50)
    nfo = _wide_ce_pe_chain(base)
    conf = [UniverseItem("ACME", "ACME", 100, "NSE", "NFO")]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], confluence_universe=conf)
    row = max((r for r in sc.snapshot("u1").rows if r.source == "confluence"),
              key=lambda r: r.timestamp_ms)
    leg = row.legs[0]
    assert leg.is_active  # the confirmed premium leg is running (entered bars ago), not fresh

    current_premium = float(premium[-1].close)
    # The stale entry-bar premium the OLD code stamped (d.spot).
    pick = OptionPick(option_symbol=leg.option_symbol, strike=leg.strike,
                      option_type=leg.option_type, expiry=leg.expiry, dte=0,
                      lot_size=leg.lot_size or 0, token=leg.token or 0)
    drows = evaluate_derivative_contract(conf[0], "ATM", pick, premium, cfg)
    d = max((x for x in drows if x.is_active or x.is_fresh), key=lambda x: x.timestamp_ms)
    assert d.spot < current_premium - 100  # entry-bar premium is materially stale
    assert leg.premium_spot == pytest.approx(current_premium)  # fix: current, not d.spot
    assert leg.premium_spot != pytest.approx(d.spot)


@pytest.mark.asyncio
async def test_scan_confluence_no_row_when_premium_does_not_confirm():
    """confluence source: the underlying fires but the option premium is flat (no
    confirming BUY) → no leg confirmed → no confluence row."""
    cfg = SterlingKiteEngineConfig()
    fired = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return fired
            return _candles(list(np.linspace(100, 101, 40)))  # flat premium → never confirms

    base = int(round(fired[-1].close / 50) * 50)
    nfo = _wide_ce_pe_chain(base)
    conf = [UniverseItem("ACME", "ACME", 100, "NSE", "NFO")]
    sc = KiteEngineScanner()
    await sc.scan(uid="u1", client=FakeClient(), universe=[], nfo_rows=nfo, bfo_rows=[],
                  cfg=cfg, moneyness=["ATM"], confluence_universe=conf)
    snap = sc.snapshot("u1")
    assert [r for r in snap.rows if r.source == "confluence"] == []


# ── signal provenance / CE+PE premium semantics regressions ──────────────────
@pytest.mark.parametrize(
    ("option_type", "expected_regime", "symbol"),
    [
        ("CE", "BULL", "HDFCBANK26JUL825CE"),
        ("PE", "BEAR", "HDFCBANK26JUL825PE"),
    ],
)
def test_derivative_option_is_long_premium_and_stamps_three_green_leg_provenance(
    option_type, expected_regime, symbol,
):
    """CE and PE derivative entries both buy a rising option premium."""
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("HDFCBANK", "HDFCBANK", 1, "NSE", "NFO")
    pick = OptionPick(option_symbol=symbol, strike=825.0, option_type=option_type,
                      expiry="2026-07-30", dte=9, lot_size=550, token=12345)
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")
    row = evaluate_derivative_contract(item, "ITM3", pick, candles, cfg)[-1]
    leg = row.legs[0]
    assert row.regime == expected_regime
    assert row.direction == "long"
    assert (leg.alignment.fast, leg.alignment.mid, leg.alignment.slow) == (1, 1, 1)
    assert leg.signal_timestamp_ms == row.timestamp_ms
    assert leg.entry_timestamp_ms == row.timestamp_ms
    assert leg.exit_state == row.exit_state


def test_grouped_derivative_rows_preserve_each_leg_timestamp_and_exit_state():
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    def make_row(symbol, token, ts, exit_state):
        alignment = AlignmentChip(fast=1, mid=1, slow=1)
        return EngineSignalRow(
            underlying="HDFCBANK", token=token, exchange="NFO", regime="BEAR",
            alignment=alignment, direction="long", option_type="PE",
            legs=[OptionLeg(moneyness="ITM3", option_type="PE", option_symbol=symbol,
                            strike=825.0 + token, expiry="2026-07-30", token=token,
                            is_active=True, signal_timestamp_ms=ts,
                            entry_timestamp_ms=ts, alignment=alignment, exit_state=exit_state)],
            spot=40.0 + token, stop_loss=30.0 + token, score=85.0,
            timestamp_ms=ts, source="derivatives", is_active=True,
        )

    grouped = _compile_rows([
        make_row("HDFCBANK_A_PE", 1, 1000, "0/3 red"),
        make_row("HDFCBANK_B_PE", 2, 2000, "1/3 red"),
    ])
    assert len(grouped) == 1
    by_symbol = {leg.option_symbol: leg for leg in grouped[0].legs}
    assert by_symbol["HDFCBANK_A_PE"].entry_timestamp_ms == 1000
    assert by_symbol["HDFCBANK_A_PE"].exit_state == "0/3 red"
    assert by_symbol["HDFCBANK_B_PE"].entry_timestamp_ms == 2000
    assert by_symbol["HDFCBANK_B_PE"].exit_state == "1/3 red"


def test_option_order_args_grouped_derivative_uses_underlying_spot_and_leg_stop():
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    row = EngineSignalRow(
        underlying="HDFCBANK", token=1, exchange="NFO", regime="BEAR",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="PE",
        legs=[
            OptionLeg(moneyness="ITM3", option_type="PE", option_symbol="LOW",
                      strike=800, expiry="2026-07-30", lot_size=550,
                      premium_spot=45, premium_sl=31),
            OptionLeg(moneyness="ATM", option_type="PE", option_symbol="ATM",
                      strike=825, expiry="2026-07-30", lot_size=550,
                      premium_spot=30, premium_sl=22),
        ],
        spot=0, underlying_spot=824, stop_loss=0, score=85, timestamp_ms=1,
        source="derivatives",
    )
    args = option_order_args(row)
    assert args["option_symbol"] == "ATM"
    assert args["stop_loss"] == 22
    assert args["stop_premium"] == 22

@pytest.mark.parametrize("option_type", ["CE", "PE"])
def test_derivative_contract_never_treats_three_red_as_an_entry(option_type):
    """The final three-red bar is never emitted as a CE/PE long-premium entry."""
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("HDFCBANK", "HDFCBANK", 1, "NSE", "NFO")
    pick = OptionPick(option_symbol=f"HDFCBANK26JUL825{option_type}", strike=825.0,
                      option_type=option_type, expiry="2026-07-30", dte=9,
                      lot_size=550, token=12345)
    candles = _trim_to_transition(_candles(_fresh_short_path()), cfg, "short")
    rows = evaluate_derivative_contract(item, "ITM3", pick, candles, cfg)
    final_ts = candles[-1].timestamp_ms
    assert all(row.timestamp_ms != final_ts for row in rows)
    assert all(row.direction == "long" for row in rows)
    assert all((row.legs[0].alignment.fast,
                row.legs[0].alignment.mid,
                row.legs[0].alignment.slow) == (1, 1, 1) for row in rows)


# ── premium hydration of candidate legs (the blank Entry/SL/TSL cells) ────────
def _deriv_row(ts, sym, strike, premium, trail):
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    return EngineSignalRow(
        underlying="INFY", token=777, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol=sym,
                        strike=strike, expiry="2026-09-29", lot_size=400,
                        premium_spot=premium, premium_sl=trail)],
        spot=premium, stop_loss=trail, entry_sl=trail * 0.9, exit_state="0/3 red",
        score=85.0, timestamp_ms=ts, source="derivatives", is_active=True,
    )


def test_compile_rows_is_idempotent_for_grouped_derivative_legs():
    """_flush() re-compiles the same accumulating list once per scanned instrument.

    The group parent used to be mutated in place (spot/stop_loss zeroed) and then have
    its leg premium re-derived from the now-zero spot, so every pass after the first
    blanked exactly one leg's Entry and TSL — which is what put "—" in those cells for
    ~1 leg of every grouped derivatives row on the live board.
    """
    rows = [_deriv_row(1000, "INFY26SEP1500CE", 1500, 42.5, 30.0),
            _deriv_row(2000, "INFY26SEP1520CE", 1520, 33.0, 22.0)]
    seen = []
    for _pass in range(3):
        compiled = _compile_rows(rows)
        assert len(compiled) == 1
        seen.append({leg.option_symbol: (leg.premium_spot, leg.premium_sl)
                     for leg in compiled[0].legs})
    assert seen[0] == seen[1] == seen[2]
    assert seen[0]["INFY26SEP1500CE"] == (42.5, 30.0)
    assert seen[0]["INFY26SEP1520CE"] == (33.0, 22.0)


def test_compile_rows_survives_being_fed_its_own_output():
    """f(f(x)) == f(x) — the property the name above only looked like it covered.

    The test above re-compiles the same RAW list repeatedly, which never exercises a
    parent row carrying more than one leg. ``held_contract_scan`` does exactly that:
    it appends held contracts to the ALREADY-compiled ``us.rows`` and re-compiles. The
    grouping step read ``r.legs[0]`` alone, so every second pass kept the first leg of
    each group and silently dropped the rest — on a live board that is strikes
    disappearing from a row, and with them the per-leg red count the exit reads.
    """
    rows = [_deriv_row(1000, "INFY26SEP1500CE", 1500, 42.5, 30.0),
            _deriv_row(2000, "INFY26SEP1520CE", 1520, 33.0, 22.0),
            _deriv_row(3000, "INFY26SEP1540CE", 1540, 21.0, 15.0)]
    once = _compile_rows(rows)
    twice = _compile_rows(once)
    assert len(twice) == 1
    assert {leg.option_symbol for leg in twice[0].legs} == {
        "INFY26SEP1500CE", "INFY26SEP1520CE", "INFY26SEP1540CE"}
    assert ({leg.option_symbol: (leg.premium_spot, leg.premium_sl) for leg in once[0].legs}
            == {leg.option_symbol: (leg.premium_spot, leg.premium_sl) for leg in twice[0].legs})


def test_compile_rows_appending_a_held_contract_keeps_the_existing_legs():
    """The held-contract pass merges one new leg into a compiled snapshot."""
    compiled = _compile_rows([_deriv_row(1000, "INFY26SEP1500CE", 1500, 42.5, 30.0),
                              _deriv_row(2000, "INFY26SEP1520CE", 1520, 33.0, 22.0)])
    held = _deriv_row(3000, "INFY26SEP1600CE", 1600, 8.0, 5.0)
    merged = _compile_rows([*compiled, held])
    assert len(merged) == 1
    assert {leg.option_symbol for leg in merged[0].legs} == {
        "INFY26SEP1500CE", "INFY26SEP1520CE", "INFY26SEP1600CE"}


def test_compile_rows_prefers_the_newer_leg_when_a_symbol_repeats():
    """A re-scanned strike replaces its older copy rather than duplicating it."""
    compiled = _compile_rows([_deriv_row(1000, "INFY26SEP1500CE", 1500, 42.5, 30.0)])
    fresher = _deriv_row(5000, "INFY26SEP1500CE", 1500, 51.0, 38.0)
    merged = _compile_rows([*compiled, fresher])
    assert len(merged) == 1
    assert len(merged[0].legs) == 1
    assert merged[0].legs[0].premium_spot == 51.0
    assert merged[0].legs[0].premium_sl == 38.0


def test_compile_rows_does_not_mutate_its_input_rows():
    rows = [_deriv_row(1000, "INFY26SEP1500CE", 1500, 42.5, 30.0)]
    _compile_rows(rows)
    assert rows[0].spot == 42.5
    assert rows[0].stop_loss == 30.0
    assert rows[0].entry_sl == pytest.approx(27.0)
    assert rows[0].legs[0].premium_spot == 42.5


def test_compile_rows_is_idempotent_for_spot_rows():
    """Non-derivative/spot rows must not duplicate when _compile_rows is called repeatedly."""
    spot_row = EngineSignalRow(
        underlying="RELIANCE", token=738561, exchange="NSE", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[], spot=2950.0, stop_loss=2900.0, entry_sl=2900.0,
        score=85.0, timestamp_ms=1000, source="spot", is_active=True,
    )
    once = _compile_rows([spot_row])
    twice = _compile_rows(once)
    thrice = _compile_rows([*once, spot_row])
    assert len(once) == 1
    assert len(twice) == 1
    assert len(thrice) == 1
    assert once[0].underlying == "RELIANCE"
    assert twice[0].underlying == "RELIANCE"
    assert thrice[0].underlying == "RELIANCE"



def test_derivative_contract_stamps_leg_premium_at_birth():
    """place_cb runs on the raw row BEFORE any grouping, so the leg must already
    carry the premium entry/trail — grouping is a display step, not a data step."""
    cfg = SterlingKiteEngineConfig()
    item = UniverseItem("HDFCBANK", "HDFCBANK", 1, "NSE", "NFO")
    pick = OptionPick(option_symbol="HDFCBANK26JUL825CE", strike=825.0,
                      option_type="CE", expiry="2026-07-30", dte=9,
                      lot_size=550, token=98765)
    rows = evaluate_derivative_contract(item, "ATM", pick, _candles(_fresh_long_path()), cfg)
    assert rows, "expected at least one premium-chart entry"
    for row in rows:
        leg = row.legs[0]
        assert leg.premium_spot == pytest.approx(row.spot)
        assert leg.premium_sl == pytest.approx(row.stop_loss)
        assert leg.token == 98765


def test_option_order_args_never_leaks_an_underlying_level_as_a_premium_stop():
    """A spot row's stop_loss is an index level (57,000), not a ₹ premium. Handing it
    to the option order path would be a stop from the wrong price domain, so the
    mapping reports None and the caller resolves a real premium stop from a quote."""
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    row = EngineSignalRow(
        underlying="NIFTY BANK", token=1, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="BANKNIFTY26AUG57100CE",
                        strike=57100, expiry="2026-08-25", lot_size=35)],
        spot=57147.5, stop_loss=56891.3, score=85.0, timestamp_ms=1, source="spot",
    )
    args = option_order_args(row)
    assert args["stop_loss"] is None
    assert args["stop_premium"] is None


def test_stamp_leg_premium_stops_uses_market_implied_iv_not_a_flat_assumption():
    """The premium SL/TSL are delta-translated from the underlying ST level, so the IV
    used to get delta decides where they land. A flat 18% is wrong for stock options,
    whose real IV runs far higher; back it out of the observed entry premium instead."""
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    def _leg_stop(entry_premium):
        row = EngineSignalRow(
            underlying="AXISBANK", token=1, exchange="NFO", regime="BEAR",
            alignment=AlignmentChip(fast=-1, mid=-1, slow=-1), direction="short", option_type="PE",
            legs=[], spot=1228.9, stop_loss=1250.0, entry_sl=1250.0,
            score=85.0, timestamp_ms=1_785_390_300_000, source="spot",
        )
        leg = OptionLeg(moneyness="ITM2", option_type="PE", option_symbol="AXISBANK26SEP1260PE",
                        strike=1260.0, expiry="2026-09-29", premium_spot=entry_premium)
        _stamp_leg_premium_stops(row, leg)
        return leg

    cheap, rich = _leg_stop(40.0), _leg_stop(80.0)
    # A richer premium implies a higher IV, hence a flatter delta and a stop that sits
    # a smaller fraction below entry. With a flat IV both would share one delta.
    assert 0 < cheap.premium_sl < cheap.premium_spot
    assert 0 < rich.premium_sl < rich.premium_spot
    assert (cheap.premium_spot - cheap.premium_sl) != pytest.approx(rich.premium_spot - rich.premium_sl)


def test_stamp_leg_premium_stops_translates_a_row_target_into_a_premium_target():
    """Navigator-originated rows carry a real AVWAP target; SuperTrend rows carry
    none, and must not grow a fabricated one."""
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    def _leg(target):
        row = EngineSignalRow(
            underlying="NIFTY 50", token=1, exchange="NFO", regime="BULL",
            alignment=AlignmentChip(fast=0, mid=0, slow=0), direction="long", option_type="CE",
            legs=[], spot=24000.0, stop_loss=23900.0, entry_sl=23900.0, target=target,
            score=50.0, timestamp_ms=1_785_390_300_000, source="navigator",
        )
        leg = OptionLeg(moneyness="ATM", option_type="CE", option_symbol="NIFTY26AUG24000CE",
                        strike=24000.0, expiry="2026-08-27", premium_spot=180.0)
        _stamp_leg_premium_stops(row, leg)
        return leg

    assert _leg(None).premium_target is None
    with_target = _leg(24300.0)
    assert with_target.premium_target is not None
    assert with_target.premium_target > with_target.premium_spot


# ── the trailing stop as a REAL exit (not a display value) ────────────────────
def _long_then_drop_path():
    """Fresh long transition (down leg then up leg, as _fresh_long_path does) which
    establishes a ratcheting trail, then a hard drop straight through it — the shape
    that used to read "0/3 red" and "running" at a double-digit open loss."""
    return _fresh_long_path() + list(np.linspace(595.0, 300.0, 8)) + [300.0] * 3


def _trail_cfg(**kw):
    # three_red_signal is the loosest counter and the live setting — under the old
    # rule it is what let a breached trade keep running.
    return SterlingKiteEngineConfig(exit_mode="three_red_signal", **kw)


def test_trail_breach_ends_the_trade_even_when_the_red_counter_has_not_fired():
    cfg = _trail_cfg()
    item = UniverseItem("NIFTY BANK", "BANKNIFTY", 260105, "NSE", "NFO")
    rows = evaluate_item(SterlingKiteEngine(cfg), item, _candles(_long_then_drop_path()), cfg)
    longs = [r for r in rows if r.direction == "long"]
    assert longs, "expected a long entry from the uptrend"
    ended = [r for r in longs if not r.is_active]
    assert ended, "a long that fell straight through its trail must not still be running"
    assert any("trail breach" in (r.exit_reason or "") for r in ended)


def test_price_stop_exit_off_restores_the_old_red_counter_only_rule():
    """The opt-out has to actually reproduce the previous behaviour, so an old study
    run stays reproducible."""
    path = _long_then_drop_path()
    item = UniverseItem("NIFTY BANK", "BANKNIFTY", 260105, "NSE", "NFO")
    on = _trail_cfg(price_stop_exit=True)
    off = _trail_cfg(price_stop_exit=False)
    rows_on = [r for r in evaluate_item(SterlingKiteEngine(on), item, _candles(path), on) if r.direction == "long"]
    rows_off = [r for r in evaluate_item(SterlingKiteEngine(off), item, _candles(path), off) if r.direction == "long"]
    assert any(not r.is_active for r in rows_on)
    assert all(r.exit_reason is None or "trail breach" not in r.exit_reason for r in rows_off)


def test_trail_exit_never_uses_the_breaking_bars_own_trail():
    """No-lookahead: the SuperTrend at bar j is computed from bar j's own high/low, so
    testing bar j against bar j's trail would let the bar that breaks the stop also be
    the bar that moved it. The level tested must be the one standing at j-1."""
    from app.engines.sterling_kite_engine.exits import trail_exit_index, trail_level

    cfg = _trail_cfg()
    candles = _candles(_long_then_drop_path())
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c_arr = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c_arr, cfg)
    longs, _shorts = entry_transitions(r)
    entry_i = int(np.where(longs)[0][0])
    last_idx = len(c_arr) - 1
    j = trail_exit_index(r, "long", entry_i, last_idx, cfg)
    assert j is not None and j > entry_i
    trail_prev = trail_level(r, "long", entry_i, j - 1, cfg)
    assert float(r.basis_low[j]) <= trail_prev
    # And the bar before it survived on the same (earlier) trail.
    if j - 1 > entry_i:
        assert float(r.basis_low[j - 1]) > trail_level(r, "long", entry_i, j - 2, cfg)


def test_trail_exit_compares_against_the_basis_the_lines_were_computed_on():
    """Under the live heikin_ashi basis the ST lines are in HA space; a raw candle low
    is a different series. RegimeSeries must carry the basis so the comparison is
    apples-to-apples."""
    cfg = SterlingKiteEngineConfig(candle_basis="heikin_ashi")
    candles = _candles(_long_then_drop_path())
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c_arr = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c_arr, cfg)
    assert r.basis_low.shape == c_arr.shape
    assert r.basis_high.shape == c_arr.shape
    # HA smooths the extremes, so the basis is genuinely not the raw candle.
    assert not np.allclose(r.basis_low, l)


def test_ended_row_freezes_its_trail_at_the_exit_bar():
    """A dead trade must not keep absorbing later bars' trail.

    Asserted as an invariance: appending a post-exit rally cannot move an already-ended
    entry's reported stop. (Not "stop below entry" — a winner's trail legitimately
    ratchets far above its entry, which is the point of a trailing stop.)
    """
    cfg = _trail_cfg()
    item = UniverseItem("NIFTY BANK", "BANKNIFTY", 260105, "NSE", "NFO")
    base = _long_then_drop_path()
    with_rally = base + list(np.linspace(305.0, 900.0, 40))

    def _dead_stops(path):
        rows = evaluate_item(SterlingKiteEngine(cfg), item, _candles(path), cfg)
        return {
            r.timestamp_ms: r.stop_loss
            for r in rows
            if r.direction == "long" and not r.is_active and "trail breach" in (r.exit_reason or "")
        }

    before, after = _dead_stops(base), _dead_stops(with_rally)
    assert before, "expected a trail-breach exit"
    for ts, stop in before.items():
        assert after.get(ts) == pytest.approx(stop), (
            "an ended entry's trail moved when later bars were appended — it is still "
            "ratcheting after the trade died"
        )


def test_ended_row_reports_the_level_that_was_actually_breached():
    """The stop shown for a dead row must be the level its own exit chip quotes.

    Read AT the exit bar, the SuperTrend has already flipped — no aligned line is
    left, `trail_level` falls back to the ENTRY bar's line, and the board printed a
    stop hundreds of points below the "trail breach (<= X)" reason beside it.
    """
    cfg = _trail_cfg()
    item = UniverseItem("NIFTY BANK", "BANKNIFTY", 260105, "NSE", "NFO")
    rows = evaluate_item(SterlingKiteEngine(cfg), item, _candles(_long_then_drop_path()), cfg)
    ended = [
        r for r in rows
        if r.direction == "long" and not r.is_active and "trail breach" in (r.exit_reason or "")
    ]
    assert ended, "expected a trail-breach exit"
    for row in ended:
        quoted = float(row.exit_reason.split("\u2264")[1].strip().rstrip(")"))
        assert row.stop_loss == pytest.approx(quoted, rel=1e-6), (
            f"row shows stop {row.stop_loss} but says it exited at {quoted}"
        )


def test_derivative_contract_also_honours_the_trail_exit():
    cfg = _trail_cfg()
    item = UniverseItem("HDFCBANK", "HDFCBANK", 1, "NSE", "NFO")
    pick = OptionPick(option_symbol="HDFCBANK26JUL825CE", strike=825.0, option_type="CE",
                      expiry="2026-07-30", dte=9, lot_size=550, token=98765)
    rows = evaluate_derivative_contract(item, "ATM", pick, _candles(_long_then_drop_path()), cfg)
    assert rows
    ended = [r for r in rows if not r.is_active]
    assert ended, "a premium that fell through its own trail must not still be running"
    assert any("trail breach" in (r.exit_reason or "") for r in ended)
    for r in ended:
        assert r.legs[0].is_active is False


def test_a_fresh_entry_can_never_be_trail_exited_on_its_own_bar():
    """Safety property for auto-exec: it fires on `is_fresh` rows, and the trail is only
    testable from the bar AFTER entry, so enforcing the trail cannot retroactively kill
    (or silently skip) a just-fired signal."""
    from app.engines.sterling_kite_engine.exits import trail_exit_index

    cfg = _trail_cfg()
    candles = _trim_to_transition(_candles(_fresh_long_path()), cfg, "long")
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c_arr = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c_arr, cfg)
    last_idx = len(c_arr) - 1
    assert trail_exit_index(r, "long", last_idx, last_idx, cfg) is None

    item = UniverseItem(name="NIFTY 50", tradingsymbol="NIFTY", token=1,
                        exchange="INDICES", option_exchange="NFO", is_index=True)
    fresh = [row for row in evaluate_item(SterlingKiteEngine(cfg), item, candles, cfg) if row.is_fresh]
    assert fresh, "expected a fresh entry on the final bar"
    assert all(row.is_active for row in fresh)
    assert all(row.exit_reason is None for row in fresh)


# ── Derivatives expiry scope ────────────────────────────────────────────────
# The derivatives pass charts the CONTRACT's own premium, so the contract has to
# outlive the 21-bar warmup. At 1H a weekly burns 3.5 of its 5 sessions on warmup
# (study/kite_st_derivatives.py), which is why `deriv_expiries` defaults to monthly.

def _two_series_chain(today):
    """A chain with one weekly and one monthly expiry in the same future month."""
    from datetime import timedelta

    weekly = (today + timedelta(days=7)).isoformat()
    monthly = (today + timedelta(days=14)).isoformat()
    # Same calendar month keeps the classifier's rule unambiguous: the latest
    # listed date in a month is the monthly, earlier ones are weeklies.
    if weekly[:7] != monthly[:7]:
        weekly = (today + timedelta(days=21)).isoformat()
        monthly = (today + timedelta(days=28)).isoformat()
    rows = []
    for expiry, base in ((weekly, 8000), (monthly, 9000)):
        for offset, kind in ((1, "CE"), (2, "PE")):
            rows.append({
                "name": "NIFTY", "tradingsymbol": f"NIFTY{expiry[8:10]}{kind}",
                "instrument_type": kind, "strike": 100, "expiry": expiry,
                "instrument_token": base + offset, "lot_size": 75,
            })
    return rows, weekly, monthly


@pytest.mark.asyncio
@pytest.mark.parametrize("deriv_expiries, expect_weekly", [
    (["monthly"], False),   # the shipped default
    (None, True),           # opt out — follow the ordinary expiry selection
])
async def test_derivative_expiry_scope_controls_which_series_is_charted(
    deriv_expiries, expect_weekly
):
    from datetime import date as _date

    today = _date.today()
    nfo, weekly, monthly = _two_series_chain(today)
    flat = _candles(list(np.linspace(100, 101, 40)))

    class FakeClient:
        async def get_candles(self, inst, resolution, limit):
            if inst.zerodha_token == 100:
                return _candles([100.0] * 40)
            return flat

    deriv = [UniverseItem("NIFTY 50", "NIFTY", 100, "INDICES", "NFO", is_index=True)]
    sc = KiteEngineScanner()
    await sc.scan(uid="u-deriv-exp", client=FakeClient(), universe=[], nfo_rows=nfo,
                  bfo_rows=[], cfg=SterlingKiteEngineConfig(), moneyness=["ATM"],
                  expiry_types=["weekly", "monthly"],
                  expiry_types_derivatives=deriv_expiries,
                  deriv_universe=deriv)
    charted = {s for s in sc.snapshot("u-deriv-exp").scanned_contract_symbols}
    assert any(monthly[8:10] in s for s in charted), charted
    assert bool(any(weekly[8:10] in s for s in charted)) is expect_weekly, charted


def test_the_derivative_expiry_default_is_monthly_only():
    """The default is a product decision, not an accident — pin it."""
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel

    assert EngineConfigModel().deriv_expiries == ["monthly"]


def test_the_derivative_scope_never_invents_a_series_the_user_excluded():
    """Narrowing is an intersection. If the user scans weeklies ONLY, a monthly
    derivative scope must not resurrect a monthly series they turned off."""
    allowed = ["weekly"]
    scope = ["monthly"]
    narrowed = [e for e in allowed if e in scope]
    assert narrowed == []          # empty → the scanner keeps `allowed` untouched
