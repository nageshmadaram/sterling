import pytest
from app.services.causal_series import latest_asof, AsOfSeries


def test_future_quote_is_rejected():
    quotes = [
        {"timestamp_ms": 1000500, "bid": 105.0, "ask": 106.0}  # 500ms in the FUTURE
    ]
    decision_ts = 1000000
    res = latest_asof(quotes, decision_ts, max_age_ms=60000)
    assert res is None, "Future quote must not be consumed"


def test_past_quote_is_selected():
    quotes = [
        {"timestamp_ms": 995000, "bid": 100.0, "ask": 101.0},  # 5s in the PAST
        {"timestamp_ms": 1000500, "bid": 105.0, "ask": 106.0}, # 500ms in the FUTURE
    ]
    decision_ts = 1000000
    res = latest_asof(quotes, decision_ts, max_age_ms=60000)
    assert res is not None
    assert res["timestamp_ms"] == 995000
    assert res["bid"] == 100.0


def test_stale_quote_is_rejected():
    quotes = [
        {"timestamp_ms": 900000, "bid": 100.0, "ask": 101.0}   # 100s old (> 60s max age)
    ]
    decision_ts = 1000000
    res = latest_asof(quotes, decision_ts, max_age_ms=60000)
    assert res is None, "Stale quote older than max_age_ms must be rejected"


def test_asof_series_ordering():
    quotes = [
        {"timestamp_ms": 990000, "bid": 98.0},
        {"timestamp_ms": 998000, "bid": 99.5},
        {"timestamp_ms": 1005000, "bid": 102.0},
    ]
    series = AsOfSeries(quotes)
    res = series.latest_at_or_before(1000000, max_age_ms=60000)
    assert res is not None
    assert res["timestamp_ms"] == 998000
    assert res["bid"] == 99.5
