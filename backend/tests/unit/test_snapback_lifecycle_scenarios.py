"""Deterministic lifecycle scenarios over the real prospective runtime.

These use fabricated fixtures — never fabricated production evidence — to drive every
downstream branch the market has not yet produced: fill accounting, hedge accounting,
the 35% premium stop, EXIT_PENDING latching, the 15-session horizon, the runner
transition and its 25% give-back, and cost accounting.

Scenario A  entry -> MTMs -> premium stop -> EXIT_PENDING -> liquidation -> CLOSED
Scenario B  entry -> 15 sessions -> >=1.5x -> RUNNER -> peak -> give-back -> CLOSED
Scenario C  runner exit with an invalid futures quote -> EXIT_PENDING stays latched
            with its original reason and bid -> closes later on a valid quote
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.engines.snapback import SnapbackConfig
from app.services.snapback import (
    process_prospective_daily_mtm_and_exits,
    process_prospective_intraday_risk,
)
from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

_IST = timezone(timedelta(hours=5, minutes=30))

ENTRY_PRICE = 100.0
OPTION_QTY = 25
FUT_LOT = 25
OPT_SYM = "NIFTY26OCT25000PE"
FUT_SYM = "NIFTY26OCTFUT"


@pytest.fixture
def warehouse(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: wh,
    )
    yield wh
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def cfg():
    return SnapbackConfig(enabled=True)


def _open_position(wh, *, entry_dt: datetime, opp_id="OPP-LIFECYCLE"):
    wh.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol=OPT_SYM,
        option_qty=OPTION_QTY,
        option_entry_price=ENTRY_PRICE,
        option_expiry="2026-10-29",
        option_strike=25000.0,
        futures_symbol=FUT_SYM,
        futures_lot_size=FUT_LOT,
        current_futures_lots=1,
        avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp=entry_dt.isoformat(),
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
    )
    return opp_id


def _quotes(stamp_ist: str, *, option_bid: float, spot=24500.0, fut_bid=24500.0, valid_fut=True):
    fut = {
        "last_price": fut_bid + 1.0,
        "buy_price": fut_bid,
        "sell_price": fut_bid + 2.0,
        "buy_quantity": 100,
        "sell_quantity": 100,
        "depth": {
            "buy": [{"price": fut_bid, "quantity": 100}],
            "sell": [{"price": fut_bid + 2.0, "quantity": 100}],
        },
        "timestamp": stamp_ist,
        "oi": 500000,
    }
    if not valid_fut:
        # A one-sided, zero-bid book: present but not executable.
        fut = {
            "last_price": fut_bid,
            "buy_price": 0.0,
            "sell_price": 0.0,
            "buy_quantity": 0,
            "sell_quantity": 0,
            "depth": {"buy": [], "sell": []},
            "timestamp": stamp_ist,
            "oi": 500000,
        }
    return {
        f"NSE:NIFTY": {"last_price": spot},
        f"NFO:{FUT_SYM}": fut,
        f"NFO:{OPT_SYM}": {
            "last_price": option_bid + 1.0,
            "buy_price": option_bid,
            "sell_price": option_bid + 2.0,
            "buy_quantity": 50,
            "sell_quantity": 50,
            "depth": {
                "buy": [{"price": option_bid, "quantity": 50}],
                "sell": [{"price": option_bid + 2.0, "quantity": 50}],
            },
            "timestamp": stamp_ist,
            "oi": 60000,
        },
    }


def _freeze(monkeypatch, when_utc: datetime):
    monkeypatch.setattr("time.time", lambda: when_utc.timestamp())

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return when_utc.astimezone(tz) if tz is not None else when_utc

    monkeypatch.setattr("app.services.snapback.datetime", Fixed)


def _client(quotes):
    client = AsyncMock()
    client.get_quote = AsyncMock(return_value=quotes)
    return client


async def _eod(monkeypatch, wh, cfg, *, when_utc: datetime, option_bid: float, **kw):
    _freeze(monkeypatch, when_utc)
    stamp = (when_utc.astimezone(_IST) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    client = _client(_quotes(stamp, option_bid=option_bid, **kw))
    return await process_prospective_daily_mtm_and_exits(client, cfg)


async def _intraday(monkeypatch, wh, cfg, *, when_utc: datetime, option_bid: float, **kw):
    _freeze(monkeypatch, when_utc)
    stamp = (when_utc.astimezone(_IST) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    client = _client(_quotes(stamp, option_bid=option_bid, **kw))
    return await process_prospective_intraday_risk(client, cfg)


def _session(day: int, hour_ist: int = 15, minute_ist: int = 29) -> datetime:
    return datetime(2026, 10, day, hour_ist, minute_ist, tzinfo=_IST).astimezone(timezone.utc)


# --------------------------------------------------------------------------- A


@pytest.mark.asyncio
async def test_scenario_a_stop_latches_then_liquidates_and_reconciles(warehouse, cfg, monkeypatch):
    opp = _open_position(warehouse, entry_dt=_session(1, 9, 20))

    # Two healthy end-of-day marks above the stop.
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(1), option_bid=110.0)
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(2), option_bid=95.0)

    marks = warehouse.get_records_by_table("daily_mtm", opportunity_id=opp)
    assert len(marks) == 2
    assert warehouse.get_paper_position(opp)["status"] == "OPEN"

    # Premium collapses through the 35% stop: the intraday monitor latches the exit.
    await _intraday(monkeypatch, warehouse, cfg, when_utc=_session(3, 11, 0), option_bid=60.0)

    pos = warehouse.get_paper_position(opp)
    assert pos["status"] == "CLOSED" or pos["pending_exit_reason"] == "PREMIUM_STOP"

    outcomes = warehouse.get_records_by_table("outcomes", opportunity_id=opp)
    assert len(outcomes) == 1
    outcome = outcomes[0]

    assert outcome["exit_reason"] == "PREMIUM_STOP"

    # Option P&L is the observed bid against the observed entry, on the real quantity.
    expected_option = (60.0 - ENTRY_PRICE) * OPTION_QTY
    assert float(outcome["actual_option_pnl"]) == pytest.approx(expected_option)

    # Total reconciles: option + futures - costs.
    total = (
        float(outcome["actual_option_pnl"])
        + float(outcome["actual_futures_pnl"])
        - float(outcome["actual_costs"])
    )
    assert float(outcome["actual_total_pnl"]) == pytest.approx(total)
    assert float(outcome["actual_costs"]) > 0


@pytest.mark.asyncio
async def test_scenario_a_eod_never_marks_a_latched_exit(warehouse, cfg, monkeypatch):
    opp = _open_position(warehouse, entry_dt=_session(1, 9, 20))

    warehouse.set_paper_position_pending_exit(
        opportunity_id=opp,
        pending_exit_reason="PREMIUM_STOP",
        pending_exit_option_bid=60.0,
        pending_exit_ts=datetime.now(timezone.utc).isoformat(),
    )

    processed = await _eod(monkeypatch, warehouse, cfg, when_utc=_session(4), option_bid=61.0)

    assert processed == 0
    assert warehouse.get_records_by_table("daily_mtm", opportunity_id=opp) == []
    assert warehouse.get_paper_position(opp)["status"] == "EXIT_PENDING"


# --------------------------------------------------------------------------- B


@pytest.mark.asyncio
async def test_scenario_b_horizon_promotes_to_runner_then_gives_back(warehouse, cfg, monkeypatch):
    opp = _open_position(warehouse, entry_dt=_session(1, 9, 20))

    # Session 15 with the premium at 1.6x: the horizon promotes rather than exits.
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(23), option_bid=160.0)

    pos = warehouse.get_paper_position(opp)
    assert pos["sessions_held"] >= 15
    assert int(pos["is_runner"]) == 1
    assert pos["status"] == "OPEN"
    assert float(pos["peak_option_bid"]) == pytest.approx(160.0)

    # The peak ratchets up.
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(26), option_bid=200.0)
    assert float(warehouse.get_paper_position(opp)["peak_option_bid"]) == pytest.approx(200.0)

    # A 25% give-back from the peak closes the runner.
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(27), option_bid=149.0)

    outcomes = warehouse.get_records_by_table("outcomes", opportunity_id=opp)
    assert len(outcomes) == 1
    assert outcomes[0]["exit_reason"] == "RUNNER_TRAIL_STOP"
    assert float(outcomes[0]["actual_option_pnl"]) == pytest.approx((149.0 - ENTRY_PRICE) * OPTION_QTY)


@pytest.mark.asyncio
async def test_scenario_b_horizon_exits_when_premium_has_not_doubled(warehouse, cfg, monkeypatch):
    opp = _open_position(warehouse, entry_dt=_session(1, 9, 20))

    # Session 15 with the premium below 1.5x: the horizon exits instead of promoting.
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(23), option_bid=120.0)

    outcomes = warehouse.get_records_by_table("outcomes", opportunity_id=opp)
    assert len(outcomes) == 1
    assert outcomes[0]["exit_reason"] == "HOLDING_HORIZON_EXPIRED"
    assert int(warehouse.get_paper_position(opp)["is_runner"]) == 0


# --------------------------------------------------------------------------- C


@pytest.mark.asyncio
async def test_scenario_c_invalid_futures_quote_keeps_the_exit_latched(warehouse, cfg, monkeypatch):
    opp = _open_position(warehouse, entry_dt=_session(1, 9, 20))

    # Promote to runner, then ratchet the peak.
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(23), option_bid=160.0)
    await _eod(monkeypatch, warehouse, cfg, when_utc=_session(26), option_bid=200.0)
    assert int(warehouse.get_paper_position(opp)["is_runner"]) == 1

    # The give-back triggers intraday, but the futures book is not executable.
    await _intraday(
        monkeypatch, warehouse, cfg,
        when_utc=_session(24, 12, 0), option_bid=149.0, valid_fut=False,
    )

    pos = warehouse.get_paper_position(opp)
    assert pos["status"] == "EXIT_PENDING"
    assert pos["pending_exit_reason"] == "RUNNER_TRAIL_STOP"
    latched_bid = float(pos["pending_exit_option_bid"])
    assert latched_bid == pytest.approx(149.0)

    # No outcome may be written without a liquidation quote.
    assert warehouse.get_records_by_table("outcomes", opportunity_id=opp) == []

    # A later valid futures quote completes the liquidation.
    await _intraday(
        monkeypatch, warehouse, cfg,
        when_utc=_session(25, 11, 0), option_bid=250.0, valid_fut=True,
    )

    outcomes = warehouse.get_records_by_table("outcomes", opportunity_id=opp)
    assert len(outcomes) == 1
    outcome = outcomes[0]

    # The original latched reason and bid decide the outcome — not the later, better bid.
    assert outcome["exit_reason"] == "RUNNER_TRAIL_STOP"
    assert float(outcome["actual_option_pnl"]) == pytest.approx((latched_bid - ENTRY_PRICE) * OPTION_QTY)
    assert warehouse.get_paper_position(opp)["status"] == "CLOSED"
