"""Scanner internals: one expiry rule, one TrueData boundary, bounded caches.

These helpers sit on the live Kite scan path that feeds the executor, and none
of them had coverage.
"""
import inspect
from datetime import date

import pytest

from app.engines.nifty_orb_options import StrategyConfig, is_monthly_expiry
from app.services import nifty_orb_scanner as scanner

# 2026 NSE calendar, verified: Aug 27 and Sep 24 are the last Thursday of their
# month and therefore monthly; Sep 3 and Sep 10 are weeklies.
AUG_MONTHLY = date(2026, 8, 27)
SEP_WEEKLY_1 = date(2026, 9, 3)
SEP_WEEKLY_2 = date(2026, 9, 10)
SEP_MONTHLY = date(2026, 9, 24)


def _eligible(*expiries):
    return [(e, {"tradingsymbol": e.isoformat()}) for e in expiries]


# --------------------------------------------------------------------------
# expiry preference
# --------------------------------------------------------------------------

def test_weekly_does_not_resolve_to_a_monthly_contract():
    """The regression: with a 0-7 day window the nearest expiry IS the monthly."""
    eligible = _eligible(AUG_MONTHLY, SEP_WEEKLY_1)
    assert scanner._expiry_for_mode(eligible, "nearest") == AUG_MONTHLY
    assert scanner._expiry_for_mode(eligible, "weekly") == SEP_WEEKLY_1


def test_monthly_picks_the_nearest_monthly_not_the_nearest_expiry():
    eligible = _eligible(SEP_WEEKLY_1, SEP_WEEKLY_2, SEP_MONTHLY)
    assert scanner._expiry_for_mode(eligible, "monthly") == SEP_MONTHLY
    assert scanner._expiry_for_mode(eligible, "nearest") == SEP_WEEKLY_1


def test_an_unmatched_preference_returns_nothing_instead_of_substituting():
    """No weekly listed must mean no trade, not a monthly trade."""
    assert scanner._expiry_for_mode(_eligible(AUG_MONTHLY), "weekly") is None
    assert scanner._expiry_for_mode(_eligible(SEP_WEEKLY_1), "monthly") is None


def test_any_takes_the_nearest_eligible_expiry():
    assert scanner._expiry_for_mode(_eligible(SEP_MONTHLY, SEP_WEEKLY_1), "any") == SEP_WEEKLY_1


def test_no_eligible_expiry_is_none():
    assert scanner._expiry_for_mode([], "nearest") is None


def test_an_unknown_preference_is_rejected():
    with pytest.raises(ValueError, match="expiry_selection must be"):
        scanner._expiry_for_mode(_eligible(AUG_MONTHLY), "fortnightly")


def test_the_scanner_shares_the_engine_expiry_rule():
    assert "is_monthly_expiry" in inspect.getsource(scanner._expiry_for_mode)
    assert is_monthly_expiry(AUG_MONTHLY) is True
    assert is_monthly_expiry(SEP_WEEKLY_1) is False


# --------------------------------------------------------------------------
# a single TrueData validation boundary
# --------------------------------------------------------------------------

def test_the_truedata_refresh_delegates_to_the_provider():
    """A second copy of these gates drifted from the provider's ordering."""
    source = inspect.getsource(scanner._truedata_refresh_option)
    assert "refresh_contract" in source
    for reimplemented in ("invalid TrueData bid/ask", "spread above configured maximum",
                          "OI below configured minimum", "max_quote_staleness_s"):
        assert reimplemented not in source


def test_liquidity_is_not_silently_relaxed_before_selection():
    """`_selection_config` looked like it relaxed gates but was a no-op."""
    assert not hasattr(scanner, "_selection_config")
    assert "_selection_config" not in inspect.getsource(scanner.scan_underlying)


# --------------------------------------------------------------------------
# cache eviction
# --------------------------------------------------------------------------

def test_expired_cache_entries_are_evicted_on_write():
    """The runner ticks forever and the option key carries the session date."""
    cache: dict = {}
    scanner._cache_put(cache, ("u1", "NIFTY", "2026-08-19"), ["old"])
    # Age the entry past the TTL without touching the clock.
    stamp, value = cache[("u1", "NIFTY", "2026-08-19")]
    cache[("u1", "NIFTY", "2026-08-19")] = (stamp - scanner._BAR_CACHE_TTL_S - 1, value)
    scanner._cache_put(cache, ("u1", "NIFTY", "2026-08-20"), ["new"])
    assert list(cache) == [("u1", "NIFTY", "2026-08-20")]


@pytest.mark.asyncio
async def test_scan_underlying_stamps_auto_block_and_fingerprint(monkeypatch):
    """The board reads auto_block off the scan row. Dropping the stamp would
    leave Manual looking clean while Auto refuses."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.engines.nifty_orb_options import Bar, OptionContract, Signal, TradePlan

    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 8, 25, 10, 30, tzinfo=ist)
    bar = Bar(timestamp=now, open=25000, high=25010, low=24990, close=25005, volume=1e6)
    sig = Signal(
        direction="LONG", regime="TREND", timestamp=now,
        or_high=25000, or_low=24900, vwap=24980, atr=20,
        breakout_distance=5, volume_ratio=1.5, confidence=0.8, reason="breakout",
    )
    opt = OptionContract(
        "NIFTY26AUG25000CE", 25000, "2026-08-27", "CE",
        ltp=18, bid=17.5, ask=18.5, lot_size=75, volume=5000, open_interest=20000,
    )
    plan = TradePlan(
        direction="LONG", option_type="CE", contract=opt,
        underlying_entry=25005, underlying_stop=24980,
        initial_risk_points=25, target_points=50,
        entry_premium=18, stop_premium=14, target_premium=26,
        premium_risk_per_share=4, quantity=75, risk_inr=300, reason="plan",
        max_loss_inr=1350, delta_is_estimated=True, delta_source="implied", delta=0.5,
    )

    async def bars(*_a, **_k):
        return [bar] * 80

    async def contracts(*_a, **_k):
        return [opt]

    monkeypatch.setattr(scanner, "_kite_bars_for_underlying", bars)
    monkeypatch.setattr(scanner, "generate_signal", lambda *_a, **_k: sig)
    monkeypatch.setattr(scanner, "_option_contracts", contracts)
    monkeypatch.setattr(scanner, "select_option", lambda *_a, **_k: opt)
    monkeypatch.setattr(scanner, "build_trade_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(
        "app.services.nifty_orb_lifecycle.preview_auto_refusal",
        lambda *_a, **_k: "daily trade limit reached",
    )
    monkeypatch.setattr("app.services.nifty_orb_execution._state", lambda uid: {"count": 2})

    cfg = StrategyConfig(enabled=True, data_source="kite")
    out = await scanner.scan_underlying("u1", "NIFTY", cfg)
    assert out["status"] == "signal"
    assert out["auto_block"] == "daily trade limit reached"
    assert out["reason"] == "daily trade limit reached"
    assert out["ticket_fingerprint"]


def test_a_live_cache_entry_survives_a_write_for_another_key():
    cache: dict = {}
    scanner._cache_put(cache, ("a",), [1])
    scanner._cache_put(cache, ("b",), [2])
    assert set(cache) == {("a",), ("b",)}


def test_the_scanner_writes_through_the_evicting_helper():
    for fn in (scanner._kite_bars_for_underlying, scanner._kite_option_contracts):
        source = inspect.getsource(fn)
        assert "_cache_put(" in source
        assert "_cache[key] = (" not in source and "_cache[cache_key] = (" not in source


# --------------------------------------------------------------------------
# the config invariant that makes the provider's early return safe
# --------------------------------------------------------------------------

def test_truedata_freshness_requires_ticks():
    with pytest.raises(ValueError, match="requires truedata_use_ticks"):
        StrategyConfig(data_source="truedata", truedata_use_ticks=False,
                       truedata_use_quote_freshness=True).validate()


def test_the_coupling_only_binds_the_truedata_source():
    StrategyConfig(data_source="kite", truedata_use_ticks=False,
                   truedata_use_quote_freshness=True).validate()
    StrategyConfig(data_source="truedata", truedata_use_ticks=False,
                   truedata_use_quote_freshness=False).validate()
