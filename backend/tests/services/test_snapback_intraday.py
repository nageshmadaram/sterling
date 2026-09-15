"""Integration boundaries for option-point Snapback research and quote plans."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import config as api
from app.engines.snapback import SnapbackConfig, descriptor, validate
from app.engines.snapback.models import IST, SnapbackSignal
from app.services import snapback, snapback_validation


CFG = SnapbackConfig(trading_mode="scalp", enabled=True)
USER = SimpleNamespace(user_id="scalp-test")


def signal():
    return SnapbackSignal(symbol="NIFTY", side="fade_up", direction="BEARISH",
                          option_type="PE", timestamp_ms=1_788_928_200_000,
                          entry=25000, mean_target=24990, stretch=2, atr=10,
                          realized_vol=0.2, assumed_iv=0.2, level=25010,
                          strength="MODERATE", reasons=("confirmed reversal",))


@pytest.mark.parametrize("values", [
    {"trading_mode": "fast"}, {"scalp_timeframe_minutes": 2},
    {"scalp_max_hold_bars": 2.5}, {"scalp_max_hold_bars": True},
    {"scalp_stop_points": float("nan")}, {"scalp_fixed_cost_inr": float("inf")},
    {"scalp_risk_pct": 3, "scalp_daily_loss_pct": 2},
    {"scalp_entry_end_minute": 920, "scalp_square_off_minute": 910},
    {"scalp_lock_points": 5}, {"scalp_runner_max_bars": 10},
    {"enabled": "false"},
])
def test_invalid_risk_time_and_boolean_settings_are_rejected(values):
    with pytest.raises(ValueError):
        validate(values, CFG)


def test_intraday_can_select_near_expiry_without_inactive_swing_horizon():
    assert validate({"min_dte": 2, "max_dte": 10}, CFG).min_dte == 2
    with pytest.raises(ValueError, match="hold_days"):
        validate({"min_dte": 2, "max_dte": 10}, SnapbackConfig())


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [11, -6, 3600])
async def test_intraday_refuses_stale_or_future_quote(age):
    client = SimpleNamespace(get_quote=AsyncMock(return_value={"NFO:OPTION": {
        "depth": {"buy": [{"price": 99}], "sell": [{"price": 100}]},
        "oi": 100_000, "timestamp": datetime.now(IST) - timedelta(seconds=age),
    }}))
    quote = await snapback._quote_for(client, {"exchange": "NFO", "symbol": "OPTION"}, CFG)
    assert quote["premium"] is None
    assert any("stale" in b for b in quote["blockers"])


@pytest.mark.asyncio
async def test_valid_buy_plan_references_ask_instead_of_mid():
    client = SimpleNamespace(get_quote=AsyncMock(return_value={"NFO:OPTION": {
        "depth": {"buy": [{"price": 99}], "sell": [{"price": 100}]},
        "oi": 100_000, "timestamp": datetime.now(IST),
    }}))
    quote = await snapback._quote_for(client, {"exchange": "NFO", "symbol": "OPTION"}, CFG)
    assert quote["premium"] == 100
    assert quote["blockers"] == []


@pytest.mark.asyncio
async def test_nonfinite_quote_cannot_become_a_plan_or_json_price():
    client = SimpleNamespace(get_quote=AsyncMock(return_value={"NFO:OPTION": {
        "depth": {"buy": [{"price": 99}], "sell": [{"price": float("inf")}]},
        "oi": float("nan"), "last_price": float("nan"), "timestamp": datetime.now(IST),
    }}))
    quote = await snapback._quote_for(client, {"exchange": "NFO", "symbol": "OPTION"}, CFG)
    assert quote["premium"] is None
    assert quote["ask"] is None and quote["ltp"] is None
    assert quote["blockers"]


def test_intraday_row_maps_real_premium_economics_without_promising_execution():
    row = snapback._row(signal(), CFG, contract={"lot_size": 50, "symbol": "OPTION"},
                       quote={"premium": 100, "bid": 99, "ask": 100}, blocked=None,
                       underlying_token=1)
    assert row["premium"] == 100
    assert row["target_premium"] > 105  # net RR after costs requires a larger target
    assert row["planned_risk_inr"] <= CFG.capital_inr * CFG.scalp_risk_pct / 100
    assert row["net_target_inr"] >= row["planned_risk_inr"]
    assert row["round_trip_cost_points"] == 1
    assert row["premium_is_modelled"] is False
    assert row["modelled_premium"] is None
    assert row["state"] == "watching" and row["execution_eligible"] is False


def test_no_intraday_quote_does_not_fall_back_to_black_scholes():
    row = snapback._row(signal(), CFG, contract={"lot_size": 50}, quote=None,
                       blocked="no quote", underlying_token=1)
    assert row["premium"] is None
    assert row["modelled_premium"] is None
    assert row["lots"] == 0


@pytest.mark.asyncio
async def test_intraday_scan_uses_minute_tape_and_quote_even_with_swing_hedge_defaults(monkeypatch):
    from app.services.exchanges.kite import accounts
    client = SimpleNamespace(search_instruments=AsyncMock(return_value=[]))
    item = SimpleNamespace(name="NIFTY", token=1, tradingsymbol="NIFTY", option_exchange="NFO")
    minute_fetch = AsyncMock(return_value=[{"time": 1}])
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: CFG)
    monkeypatch.setattr(accounts, "get_active", lambda uid: object())
    monkeypatch.setattr(accounts, "acquire_client", AsyncMock(return_value=client))
    monkeypatch.setattr(snapback, "resolve_universe", lambda *a, **k: [item])
    monkeypatch.setattr(snapback, "_intraday_candles", minute_fetch)
    monkeypatch.setattr(snapback, "_candles", AsyncMock(side_effect=AssertionError("daily candles used")))
    monkeypatch.setattr(snapback, "evaluate_symbol", lambda *a, **k: [signal()])
    monkeypatch.setattr(snapback, "_contract_for", AsyncMock(return_value={"lot_size": 50, "symbol": "OPTION"}))
    monkeypatch.setattr(snapback, "_quote_for", AsyncMock(return_value={
        "premium": 100, "bid": 99, "ask": 100, "blockers": [],
    }))
    snapback._state.pop("scan-intraday-test", None)
    body = await snapback.scan_once("scan-intraday-test")
    assert minute_fetch.await_count == 1
    assert body["failures"] == []
    assert body["scanned"] == 1 and len(body["rows"]) == 1
    assert body["rows"][0]["premium"] == 100
    assert body["rows"][0]["trading_mode"] == "scalp"
    assert body["rows"][0]["execution_eligible"] is False


def test_daily_history_and_promotion_cannot_be_reused_for_intraday(monkeypatch):
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: CFG)
    monkeypatch.setattr(snapback_validation, "load", lambda: {
        "promoted": True, **snapback_validation.current_manifest(CFG),
    })
    assert snapback.recent_signals(USER.user_id) == []
    assert descriptor(CFG)["validated"] is False
    assert descriptor(CFG)["calibrated_fields"] == []
    assert "intraday" in snapback_validation.auto_execution_blocker(CFG)
    body = snapback.snapshot(USER.user_id)
    assert body["capabilities"]["option_tape_replay"] is True
    assert body["capabilities"]["replay"] is False
    assert body["capabilities"]["manual_execution"]["single_leg"] is False


@pytest.mark.asyncio
async def test_timeframe_specific_cache_cannot_reuse_daily_or_other_minute_bars():
    st = snapback.ScanState()
    client = SimpleNamespace(get_candles=AsyncMock(side_effect=[["daily"], ["1m"], ["5m"]]))
    assert await snapback._candles(client, st, 1, "NIFTY") == ["daily"]
    assert await snapback._intraday_candles(client, st, 1, "NIFTY", CFG) == ["1m"]
    assert await snapback._intraday_candles(client, st, 1, "NIFTY", replace(CFG, scalp_timeframe_minutes=5)) == ["5m"]
    assert await snapback._intraday_candles(client, st, 1, "NIFTY", CFG) == ["1m"]
    assert [c.args[1] for c in client.get_candles.call_args_list] == ["1D", "1m", "5m"]


def request_data():
    start = datetime(2026, 9, 14, 9, 15, tzinfo=IST).timestamp()
    rows = [dict(time=start + i * 60, open=100, high=101, low=99, close=100) for i in range(35)]
    return dict(symbol="NIFTY", option_symbol="TEST_OPTION", option_type="PE", lot_size=50,
                underlying=rows, option_candles=[dict(r) for r in rows], settings={"trading_mode": "scalp"})


def test_replay_endpoint_does_not_save_config_or_claim_validation(monkeypatch):
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: SnapbackConfig())
    monkeypatch.setattr(snapback, "set_config", lambda *a, **k: pytest.fail("research must not persist settings"))
    result = api.snapback_replay_intraday(api.SnapbackIntradayReplayRequest(**request_data()), USER)
    assert result["research_only"] is True
    assert result["validation_status"] == "unvalidated_intraday_research"
    assert result["trade_count"] == 0
    assert "not independently verified" in result["data_source"]


@pytest.mark.parametrize("kind", ["duplicate", "missing", "ohlc"])
def test_replay_endpoint_refuses_sample_corruption(monkeypatch, kind):
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: CFG)
    body = request_data()
    if kind == "duplicate":
        body["option_candles"][5]["time"] = body["option_candles"][4]["time"]
    elif kind == "missing":
        body["option_candles"].pop(5)
    else:
        body["option_candles"][5]["high"] = 90
    with pytest.raises(HTTPException) as error:
        api.snapback_replay_intraday(api.SnapbackIntradayReplayRequest(**body), USER)
    assert error.value.status_code == 422


def test_replay_schema_rejects_nonfinite_prices_and_fractional_lots():
    body = request_data()
    body["underlying"][0]["open"] = float("nan")
    with pytest.raises(ValidationError):
        api.SnapbackIntradayReplayRequest(**body)
    body = request_data()
    body["lot_size"] = 1.5
    with pytest.raises(ValidationError):
        api.SnapbackIntradayReplayRequest(**body)
