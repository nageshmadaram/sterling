"""The T+1 hedge needs a causal beta. The previous import target did not exist, so
every non-NIFTY entry was skipped with 'missing causal rolling beta' — a silent
zero-fill month. These tests pin the replacement.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.snapback import _causal_daily_bars

_IST = timezone(timedelta(hours=5, minutes=30))


def _bar(day: date, close: float):
    ts = datetime(day.year, day.month, day.day, 15, 30, tzinfo=_IST)
    return SimpleNamespace(
        timestamp_ms=int(ts.timestamp() * 1000),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
    )


def _client(bars):
    client = AsyncMock()
    client.search_instruments = AsyncMock(
        return_value=[
            {"tradingsymbol": "LAURUSLABS", "instrument_token": 4923905},
            {"tradingsymbol": "NIFTY 50", "instrument_token": 256265},
        ]
    )
    client.get_candles = AsyncMock(return_value=bars)
    return client


@pytest.mark.asyncio
async def test_bars_are_truncated_at_the_signal_date():
    bars = [_bar(date(2026, 9, d), 100.0 + d) for d in (11, 14, 15, 16, 17)]
    client = _client(bars)

    out = await _causal_daily_bars(client, "LAURUSLABS", date(2026, 9, 15), cache={})

    # Nothing after the signal session may be visible at entry.
    assert len(out) == 3
    assert out[-1].close == pytest.approx(115.0)


@pytest.mark.asyncio
async def test_signal_day_bar_is_included():
    bars = [_bar(date(2026, 9, 15), 115.0)]
    client = _client(bars)

    out = await _causal_daily_bars(client, "LAURUSLABS", date(2026, 9, 15), cache={})

    assert len(out) == 1


@pytest.mark.asyncio
async def test_unknown_symbol_returns_no_bars_rather_than_guessing():
    client = _client([_bar(date(2026, 9, 15), 100.0)])
    client.search_instruments = AsyncMock(return_value=[])

    out = await _causal_daily_bars(client, "NOSUCHNAME", date(2026, 9, 15), cache={})

    assert out == []


@pytest.mark.asyncio
async def test_instrument_dump_is_fetched_once_per_cycle():
    bars = [_bar(date(2026, 9, 15), 100.0)]
    client = _client(bars)
    cache: dict = {}

    await _causal_daily_bars(client, "LAURUSLABS", date(2026, 9, 15), cache=cache)
    await _causal_daily_bars(client, "NIFTY 50", date(2026, 9, 15), cache=cache)

    assert client.search_instruments.await_count == 1


@pytest.mark.asyncio
async def test_dict_candles_are_supported():
    client = _client(
        [
            {
                "timestamp_ms": int(
                    datetime(2026, 9, 15, 15, 30, tzinfo=_IST).timestamp() * 1000
                ),
                "close": 100.0,
            }
        ]
    )

    out = await _causal_daily_bars(client, "LAURUSLABS", date(2026, 9, 15), cache={})

    assert len(out) == 1


def test_entry_path_no_longer_imports_a_missing_symbol():
    import inspect

    from app.services import snapback as sb

    source = inspect.getsource(sb.process_prospective_pending_entries)

    assert "get_daily_bars" not in source
    assert "_causal_daily_bars" in source
