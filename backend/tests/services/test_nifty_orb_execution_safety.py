from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services.nifty_orb_execution import _conservative_quantity, _entry_window_open, _market_open, _parse_timestamp, _quote_age

IST = ZoneInfo("Asia/Kolkata")


def test_conservative_quantity_is_lot_aligned_and_never_exceeds_premium_budget():
    assert _conservative_quantity(225, 75, 40.0, 3000.0) == 75
    assert _conservative_quantity(225, 75, 20.0, 3000.0) == 150
    assert _conservative_quantity(150, 75, 20.0, 3000.0) == 150


def test_timestamp_parser_normalizes_naive_and_utc_timestamps():
    naive = _parse_timestamp("2026-08-19T10:00:00")
    utc = _parse_timestamp("2026-08-19T04:30:00Z")
    assert naive.tzinfo is not None
    assert utc.tzinfo is not None
    assert naive.astimezone(ZoneInfo("UTC")) == utc.astimezone(ZoneInfo("UTC"))


def test_quote_age_rejects_missing_timestamp_semantically():
    assert _quote_age(None) is None


def test_quote_age_is_non_negative_for_recent_quote():
    now = datetime.now(IST)
    assert 0 <= _quote_age(now.isoformat()) < 2


def test_execution_rechecks_configured_entry_window():
    cfg = SimpleNamespace(entry_start="09:30", entry_end="12:00")
    assert _entry_window_open(datetime(2026, 8, 19, 9, 29, tzinfo=IST), cfg) is False
    assert _entry_window_open(datetime(2026, 8, 19, 9, 30, tzinfo=IST), cfg) is True
    assert _entry_window_open(datetime(2026, 8, 19, 12, 1, tzinfo=IST), cfg) is False


def test_market_open_uses_the_nse_holiday_calendar():
    session = datetime(2026, 8, 25, 10, 30, tzinfo=IST)
    holiday = datetime(2026, 1, 26, 10, 30, tzinfo=IST)  # Republic Day, a Monday
    saturday = datetime(2026, 8, 29, 10, 30, tzinfo=IST)
    assert _market_open(session) is True
    assert _market_open(holiday) is False
    assert _market_open(saturday) is False


def test_arm_does_not_invent_a_half_delta():
    import inspect
    from app.services import nifty_orb_execution
    src = inspect.getsource(nifty_orb_execution.execute_scan)
    assert "or 0.5" not in src
