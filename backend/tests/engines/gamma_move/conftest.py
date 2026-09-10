"""Bar builders shared by the Gamma Move engine tests."""
from __future__ import annotations

import pytest

from app.engines.gamma_move import Candle, InstrumentRef, OICandle, SpotLevel, StrikeCandidate

#: A Monday 09:15 IST, so day arithmetic in the tests is not fighting a weekend.
BASE_MS = 1_789_009_200_000
DAY_MS = 86_400_000
BAR_MS = 900_000


def bar(day: int, i: int, *, oi: int = 100_000, volume: int = 1_000,
        close: float = 50.0, low: float | None = None) -> OICandle:
    return OICandle(ts_ms=BASE_MS + day * DAY_MS + i * BAR_MS,
                    open=close, high=close, low=low if low is not None else close * 0.9,
                    close=close, volume=volume, oi=oi)


def quiet_session(day: int = 0, n: int = 24, **kw) -> list:
    """A flat, low-volume session: nothing in it can trigger anything."""
    return [bar(day, i, **kw) for i in range(n)]


@pytest.fixture
def instrument() -> InstrumentRef:
    return InstrumentRef(instrument_id="12345", tradingsymbol="RELIANCE26SEP1300CE",
                         option_type="CE", strike=1300.0, expiry="2026-09-29",
                         lot_size=500, tick_size=0.05)


@pytest.fixture
def level() -> SpotLevel:
    return SpotLevel(price=1300.0, kind="resistance", touches=3, last_touch_ms=BASE_MS)


@pytest.fixture
def candidate(instrument, level) -> StrikeCandidate:
    return StrikeCandidate(underlying="RELIANCE", level=level, instrument=instrument,
                           oi=6_000_000, days_to_expiry=9, spot=1298.0, premium=53.0,
                           chain_oi_max=6_000_000)


@pytest.fixture
def rising_spot() -> list:
    return [Candle(ts_ms=BASE_MS + i * DAY_MS, open=100 + i, high=102 + i,
                   low=99 + i, close=101 + i) for i in range(40)]


@pytest.fixture
def falling_spot() -> list:
    return [Candle(ts_ms=BASE_MS + i * DAY_MS, open=200 - i, high=202 - i,
                   low=199 - i, close=200 - i) for i in range(40)]
