from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.engines.option_contracts import Bar
from app.engines.opening_volume_leaders import LeaderDirection, evaluate_leader
from app.services import opening_volume_leaders as service

IST = timezone(timedelta(hours=5, minutes=30))
REFERENCE_LEADERS = {"GODREJCP", "RBLBANK", "SOLARINDS", "INOXWIND", "PAGEIND"}


class FakeInstrumentClient:
    def __init__(self, fno_symbols=REFERENCE_LEADERS):
        self.fno_symbols = set(fno_symbols)

    async def search_instruments(self, _query, exchange, limit=50):
        assert limit == service._INSTRUMENT_MASTER_LIMIT
        if exchange == "NFO":
            rows = [
                {"name": symbol, "instrument_type": option_type}
                for symbol in sorted(self.fno_symbols)
                for option_type in ("CE", "PE")
            ]
            rows.extend(
                [
                    {"name": "NIFTY", "instrument_type": "CE"},
                    {"name": "FUTUREONLY", "instrument_type": "FUT"},
                ]
            )
            return rows
        if exchange == "NSE":
            return [
                {"tradingsymbol": symbol, "instrument_type": "EQ"}
                for symbol in sorted(self.fno_symbols | {"CASHONLY"})
            ]
        raise AssertionError(f"unexpected exchange: {exchange}")


def _sessions(count: int, before: date) -> list[date]:
    rows: list[date] = []
    cursor = before
    while len(rows) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            rows.append(cursor)
    return list(reversed(rows))


def _bars() -> list[Bar]:
    session = date(2026, 9, 3)
    rows: list[Bar] = []
    for day in _sessions(20, session):
        for minute in range(300):
            timestamp = datetime.combine(day, time(9, 15), tzinfo=IST) + timedelta(
                minutes=minute
            )
            rows.append(Bar(timestamp, 110.0, 111.0, 109.0, 110.5, 2_000.0))
    rows.extend(
        [
            Bar(
                datetime(2026, 9, 3, 9, 15, tzinfo=IST),
                110.0,
                112.0,
                109.5,
                111.8,
                10_000.0,
            ),
            Bar(
                datetime(2026, 9, 3, 9, 16, tzinfo=IST),
                111.8,
                113.0,
                111.5,
                112.8,
                4_000.0,
            ),
        ]
    )
    return rows


def test_live_config_rejects_an_empty_custom_universe():
    with pytest.raises(ValueError, match="select symbols"):
        service.LiveLeaderScanConfig(scan_all_stocks=False).validate()


@pytest.mark.asyncio
async def test_runtime_rejects_weekends_and_preopen_before_broker_io():
    with pytest.raises(ValueError, match="weekday"):
        await service.scan_kite_leaders(
            "tenant-a",
            as_of=datetime(2026, 9, 5, 10, 0, tzinfo=IST),
        )
    with pytest.raises(ValueError, match="not complete"):
        await service.scan_kite_leaders(
            "tenant-a",
            as_of=datetime(2026, 9, 4, 9, 15, tzinfo=IST),
        )


@pytest.mark.asyncio
async def test_broker_discovery_includes_reference_leaders_and_excludes_indices():
    symbols = await service._discover_fno_equity_symbols(FakeInstrumentClient())

    assert REFERENCE_LEADERS <= set(symbols)
    assert "NIFTY" not in symbols
    assert "FUTUREONLY" not in symbols
    assert "CASHONLY" not in symbols


@pytest.mark.asyncio
async def test_full_universe_is_current_broker_fno_not_the_legacy_curated_subset():
    cfg = service.LiveLeaderScanConfig(max_candidates=500)
    symbols, metadata = await service._resolve_universe(
        FakeInstrumentClient(),
        cfg,
    )

    assert set(symbols) == REFERENCE_LEADERS
    assert metadata == {
        "source": "kite_nfo_options_intersect_nse_equities",
        "available_fno_equity_count": 5,
        "requested_count": 5,
        "selected_count": 5,
        "truncated": False,
        "symbols": sorted(REFERENCE_LEADERS),
    }


@pytest.mark.asyncio
async def test_custom_universe_accepts_current_fno_names_and_rejects_non_fno_names():
    cfg = service.LiveLeaderScanConfig(
        scan_all_stocks=False,
        symbols=("RBLBANK", "THIN-NAME"),
    )
    with pytest.raises(ValueError, match="THIN-NAME"):
        await service._resolve_universe(FakeInstrumentClient(), cfg)

    valid, metadata = await service._resolve_universe(
        FakeInstrumentClient(),
        service.LiveLeaderScanConfig(
            scan_all_stocks=False,
            symbols=("rblbank", "RBLBANK"),
        ),
    )
    assert valid == ["RBLBANK"]
    assert metadata["source"] == "explicit_current_fno_equities"


@pytest.mark.asyncio
async def test_universe_cap_is_explicitly_reported_as_truncation():
    symbols, metadata = await service._resolve_universe(
        FakeInstrumentClient(),
        service.LiveLeaderScanConfig(max_candidates=2),
    )

    assert len(symbols) == 2
    assert metadata["available_fno_equity_count"] == 5
    assert metadata["selected_count"] == 2
    assert metadata["truncated"] is True
    assert metadata["symbols"] == symbols


@pytest.mark.asyncio
async def test_history_cache_rolls_forward_when_another_minute_completes(monkeypatch):
    service._history_cache.clear()

    class FakeClient:
        calls = 0

        async def get_historical(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "candles": [["2026-09-03 09:15:00", 100.0, 101.0, 99.0, 100.5, 1_000.0]]
            }

    async def no_wait():
        return None

    monkeypatch.setattr(service._historical_pacer, "wait", no_wait)
    fake_client = FakeClient()
    common = {
        "client": fake_client,
        "uid": "tenant-a",
        "symbol": "RELIANCE",
        "token": 123,
        "history_calendar_days": 45,
    }

    await service._history(
        **common,
        as_of=datetime(2026, 9, 3, 9, 16, 10, tzinfo=IST),
    )
    await service._history(
        **common,
        as_of=datetime(2026, 9, 3, 9, 16, 50, tzinfo=IST),
    )
    assert fake_client.calls == 1

    await service._history(
        **common,
        as_of=datetime(2026, 9, 3, 9, 17, 0, tzinfo=IST),
    )
    assert fake_client.calls == 2
    service._history_cache.clear()


def test_breadth_uses_the_documented_one_point_five_ratio():
    signal = evaluate_leader(
        "UP",
        _bars(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )
    breadth = service._breadth(
        [
            signal,
            replace(signal, symbol="UP2", day_change_pct=1.0),
            replace(signal, symbol="UP3", day_change_pct=2.0),
            replace(
                signal,
                symbol="DOWN",
                direction=LeaderDirection.DOWN,
                day_change_pct=-1.0,
            ),
        ]
    )

    assert breadth["mood"] == "bullish"
    assert breadth["green_pct"] == pytest.approx(75.0)
    assert breadth["participation"] == "strong_green"


def test_sector_tailwind_uses_explicit_mapping_and_never_guesses_membership():
    signal = evaluate_leader(
        "UP",
        _bars(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )
    rows = [
        signal,
        replace(signal, symbol="UP2", day_change_pct=1.0),
        replace(signal, symbol="UNKNOWN", day_change_pct=1.0),
    ]

    result = service._sector_alignments(
        rows,
        {"UP": "BANKS", "UP2": "BANKS"},
    )

    assert result == {"UP": True, "UP2": True, "UNKNOWN": None}


def test_playbook_keeps_private_gates_unverified():
    signal = evaluate_leader(
        "RELIANCE",
        _bars(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )
    context = service._playbook_context(
        signal,
        {
            "mood": "bullish",
        },
    )

    assert context["breadth_alignment"] == "aligned"
    assert context["primary_gate_complete"] is False
    assert context["unverified_private_gates"] == [
        "ORION score >=55",
        "ORION conviction >=5/7",
        "ORION hidden amber/LATE predicates",
    ]


@pytest.mark.asyncio
async def test_best_option_is_nearest_directional_strike_with_risk_levels():
    signal = evaluate_leader(
        "RELIANCE",
        _bars(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )

    class QuoteClient:
        async def get_quote(self, keys):
            assert keys == ["NFO:RELIANCE26SEP115CE"]
            return {
                keys[0]: {
                    "last_price": 50.0,
                    "depth": {
                        "buy": [{"price": 49.5}],
                        "sell": [{"price": 50.5}],
                    },
                }
            }

    nfo_rows = [
        {
            "name": "RELIANCE",
            "instrument_type": option_type,
            "exchange": "NFO",
            "tradingsymbol": f"RELIANCE26SEP{strike}{option_type}",
            "expiry": "2026-09-24",
            "strike": strike,
            "lot_size": 250,
        }
        for option_type in ("CE", "PE")
        for strike in (110, 115)
    ]
    result = await service._best_option_payloads(
        QuoteClient(),
        [signal],
        nfo_rows,
        session_date=date(2026, 9, 3),
        spot_prices={"RELIANCE": 112.8},
    )
    option = result["RELIANCE"]["option"]

    assert option is not None
    assert option["tradingsymbol"] == "RELIANCE26SEP115CE"
    assert option["option_type"] == "CE"
    assert option["lot_cost"] == pytest.approx(12_500.0)
    assert option["premium_stop_price"] == pytest.approx(35.0)
    assert option["premium_target_price"] == pytest.approx(75.0)
    assert option["premium_risk_per_lot"] == pytest.approx(3_750.0)


@pytest.mark.asyncio
async def test_daily_market_context_excludes_forming_day(monkeypatch):
    signal = evaluate_leader(
        "RELIANCE",
        _bars(),
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000.0,
    )
    sessions = _sessions(60, date(2026, 9, 3))
    candles = [
        [
            datetime.combine(session, time(0), tzinfo=IST).isoformat(),
            90.0,
            120.0 + index,
            80.0,
            100.0 + index / 10,
            1_000.0,
        ]
        for index, session in enumerate(sessions)
    ]
    candles.append(
        [
            "2026-09-03T00:00:00+05:30",
            1.0,
            9_999.0,
            1.0,
            9_999.0,
            1.0,
        ]
    )

    class DailyClient:
        async def get_historical(self, token, interval, *_args):
            assert token == 123
            assert interval == "day"
            return {"candles": candles}

    async def no_wait():
        return None

    service._daily_cache.clear()
    monkeypatch.setattr(service._historical_pacer, "wait", no_wait)
    context = await service._daily_market_context(
        DailyClient(),
        uid="tenant-a",
        symbol="RELIANCE",
        token=123,
        signal=signal,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
    )

    assert context["status"] == "available"
    assert context["daily_session_count"] == 60
    assert context["sma_50"] is not None
    assert context["high_52w"] < 9_999.0
    assert context["trend_50dma_aligned"] is True
    service._daily_cache.clear()


@pytest.mark.asyncio
async def test_kite_runtime_returns_advisory_leaders_without_execution(monkeypatch):
    from app.services.exchanges.kite import accounts

    class FakeClient:
        async def search_instruments(self, _query, exchange, limit=50):
            assert limit == service._INSTRUMENT_MASTER_LIMIT
            if exchange == "NFO":
                return [{"name": "RELIANCE", "instrument_type": "CE"}]
            if exchange == "NSE":
                return [{"tradingsymbol": "RELIANCE", "instrument_type": "EQ"}]
            raise AssertionError(f"unexpected exchange: {exchange}")

    fake_client = FakeClient()
    monkeypatch.setattr(
        accounts, "get_active", lambda uid: SimpleNamespace(user_id=uid)
    )

    async def acquire_client(_account):
        return fake_client

    async def instrument(_client, symbol):
        assert _client is fake_client
        assert symbol == "RELIANCE"
        return SimpleNamespace(zerodha_token=123)

    async def history(_client, **kwargs):
        assert _client is fake_client
        assert kwargs["uid"] == "tenant-a"
        assert kwargs["token"] == 123
        return _bars()

    monkeypatch.setattr(accounts, "acquire_client", acquire_client)
    monkeypatch.setattr(service, "_kite_instrument", instrument)
    monkeypatch.setattr(service, "_history", history)

    result = await service.scan_kite_leaders(
        "tenant-a",
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        scan_config=service.LiveLeaderScanConfig(
            scan_all_stocks=False,
            symbols=("RELIANCE",),
        ),
    )

    assert result["strategy"]["execution"] == "guarded_account_mode"
    assert result["universe"]["source"] == "explicit_current_fno_equities"
    assert result["universe"]["available_fno_equity_count"] == 1
    assert result["universe_count"] == 1
    assert result["evaluated_count"] == 1
    assert result["event_count"] == 1
    assert result["pending_orb_count"] == 0
    assert result["leader_count"] == 1
    assert result["leaders"][0]["symbol"] == "RELIANCE"
    assert result["leaders"][0]["tier"] == "strong"
    assert result["leaders"][0]["decision"]["model"] == (
        "sterling_opening_decision_v1"
    )
    assert result["leaders"][0]["decision"]["execution_eligible"] is False
    assert result["leaders"][0]["live_price"] is None
    assert (
        result["leaders"][0]["option_status"]
        == "historical_quote_unavailable"
    )
    assert result["failures"] == []


@pytest.mark.asyncio
async def test_runtime_keeps_prebreak_stocks_in_breadth_but_not_event_cards(monkeypatch):
    from app.services.exchanges.kite import accounts

    class FakeClient:
        async def search_instruments(self, _query, exchange, limit=50):
            if exchange == "NFO":
                return [{"name": "RELIANCE", "instrument_type": "CE"}]
            return [{"tradingsymbol": "RELIANCE", "instrument_type": "EQ"}]

    fake_client = FakeClient()
    monkeypatch.setattr(accounts, "get_active", lambda uid: SimpleNamespace(user_id=uid))

    async def acquire_client(_account):
        return fake_client

    async def instrument(_client, _symbol):
        return SimpleNamespace(zerodha_token=123)

    async def history(_client, **_kwargs):
        # Preserve the 5x 09:15 signal, but remove the 09:16 range breach.
        rows = _bars()
        last = rows[-1]
        rows[-1] = replace(last, high=111.9, low=110.0, close=111.0)
        return rows

    monkeypatch.setattr(accounts, "acquire_client", acquire_client)
    monkeypatch.setattr(service, "_kite_instrument", instrument)
    monkeypatch.setattr(service, "_history", history)

    result = await service.scan_kite_leaders(
        "tenant-a",
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        scan_config=service.LiveLeaderScanConfig(include_watch=True, include_weak=True),
    )

    assert result["evaluated_count"] == 1
    assert result["event_count"] == 0
    assert result["pending_orb_count"] == 1
    assert result["breadth"]["coverage_pct"] == 100.0
    assert result["leaders"] == []
    assert result["watch"] == []
    assert result["weak"] == []


@pytest.mark.asyncio
async def test_full_runtime_evaluates_every_broker_discovered_reference_leader(
    monkeypatch,
):
    from app.services.exchanges.kite import accounts

    fake_client = FakeInstrumentClient()
    monkeypatch.setattr(
        accounts, "get_active", lambda uid: SimpleNamespace(user_id=uid)
    )

    async def acquire_client(_account):
        return fake_client

    async def instrument(_client, symbol):
        assert _client is fake_client
        assert symbol in REFERENCE_LEADERS
        return SimpleNamespace(zerodha_token=abs(hash(symbol)) or 1)

    async def history(_client, **kwargs):
        assert _client is fake_client
        assert kwargs["symbol"] in REFERENCE_LEADERS
        return _bars()

    monkeypatch.setattr(accounts, "acquire_client", acquire_client)
    monkeypatch.setattr(service, "_kite_instrument", instrument)
    monkeypatch.setattr(service, "_history", history)

    result = await service.scan_kite_leaders(
        "tenant-a",
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        scan_config=service.LiveLeaderScanConfig(max_candidates=500),
    )

    assert result["universe"]["source"] == ("kite_nfo_options_intersect_nse_equities")
    assert result["universe_count"] == len(REFERENCE_LEADERS)
    assert result["evaluated_count"] == len(REFERENCE_LEADERS)
    assert {row["symbol"] for row in result["leaders"]} == REFERENCE_LEADERS
    assert result["failures"] == []
