"""The evidence tick keeps what cannot be recovered later.

Two facts are destroyed forever by the raw tick path and are the reason this
schema exists: depth beyond level 1, and the difference between an exchange clock
and ours. Both are tested here as absences that must not reappear.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from kitelake.config import IST
from kitelake.evidence import (
    DEPTH_LEVELS,
    EVIDENCE_TICK_SCHEMA,
    SOURCE_KITE_FULL,
    evidence_tick_row,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)


def _tick(**over):
    tick = {
        "instrument_token": 12345,
        "last_price": 101.5,
        "last_traded_quantity": 75,
        "volume_traded": 120_000,
        "oi": 4_500,
        "exchange_timestamp": datetime(2026, 9, 17, 14, 49, 59),  # naive IST
        "depth": {
            "buy": [{"price": 101.0 - i * 0.5, "quantity": 50 + i, "orders": 3 + i} for i in range(5)],
            "sell": [{"price": 102.0 + i * 0.5, "quantity": 25 + i, "orders": 2 + i} for i in range(5)],
        },
    }
    tick.update(over)
    return tick


def _row(**kw):
    kw.setdefault("received_ts", NOW)
    kw.setdefault("evidence_class", "OBSERVED_MARKET")
    return evidence_tick_row(kw.pop("tick", _tick()), **kw)


def test_all_five_depth_levels_survive():
    row = _row()

    for i in range(DEPTH_LEVELS):
        assert row[f"bid{i}_price"] is not None, f"bid level {i} lost"
        assert row[f"ask{i}_price"] is not None, f"ask level {i} lost"

    # Ordering preserved, not just presence.
    assert row["bid0_price"] > row["bid1_price"] > row["bid4_price"]
    assert row["ask0_price"] < row["ask1_price"] < row["ask4_price"]


def test_order_counts_are_kept():
    row = _row()

    assert row["bid0_orders"] == 3
    assert row["ask4_orders"] == 6


def test_a_missing_level_is_null_not_zero():
    tick = _tick(depth={"buy": [{"price": 101.0, "quantity": 50, "orders": 3}], "sell": []})

    row = _row(tick=tick)

    # An absent level and a level quoting zero are different facts.
    assert row["bid0_price"] is not None
    assert row["bid1_price"] is None
    assert row["bid1_qty"] is None
    assert row["ask0_price"] is None


def test_an_empty_book_is_all_null():
    row = _row(tick=_tick(depth={}))

    for i in range(DEPTH_LEVELS):
        assert row[f"bid{i}_price"] is None
        assert row[f"ask{i}_price"] is None


def test_both_clocks_are_recorded_separately():
    row = _row()

    assert row["received_ts"] == NOW
    # Naive IST in, UTC out: 14:49:59 IST is 09:19:59 UTC.
    assert row["exchange_ts"] == datetime(2026, 9, 17, 9, 19, 59, tzinfo=timezone.utc)
    assert row["exchange_ts"] != row["received_ts"]


def test_freshness_is_computable_from_the_row():
    row = _row()

    age = row["received_ts"] - row["exchange_ts"]

    assert age == timedelta(seconds=1)


def test_a_missing_exchange_clock_stays_missing():
    """The raw path substitutes now(); that makes a fabricated stamp look real."""
    row = _row(tick=_tick(exchange_timestamp=None))

    assert row["exchange_ts"] is None
    assert row["received_ts"] == NOW


def test_a_nonsense_exchange_clock_is_not_coerced():
    row = _row(tick=_tick(exchange_timestamp="2026-09-17 14:49:59"))

    assert row["exchange_ts"] is None


def test_an_aware_exchange_clock_is_respected():
    stamp = datetime(2026, 9, 17, 14, 49, 59, tzinfo=IST)

    row = _row(tick=_tick(exchange_timestamp=stamp))

    assert row["exchange_ts"] == datetime(2026, 9, 17, 9, 19, 59, tzinfo=timezone.utc)


def test_provenance_is_mandatory():
    with pytest.raises(ValueError) as excinfo:
        _row(evidence_class=None)

    assert "provenance" in str(excinfo.value).lower()


def test_an_unknown_provenance_is_refused():
    with pytest.raises(ValueError):
        _row(evidence_class="REAL")


def test_identity_of_the_recording_code_is_carried():
    row = _row(
        opportunity_id="OPP-1",
        capture_reason="SELECTED_OPTION",
        runtime_build_sha="9a706f584",
        strategy_config_hash="6ecbeb53e9768a91",
        strategy_rule_hash="e03ddf75f29463a8",
    )

    assert row["opportunity_id"] == "OPP-1"
    assert row["capture_reason"] == "SELECTED_OPTION"
    assert row["runtime_build_sha"] == "9a706f584"
    assert row["source"] == SOURCE_KITE_FULL


def test_the_underlying_is_recorded_beside_the_option():
    spot_ts = datetime(2026, 9, 17, 14, 49, 58)

    row = _row(spot_observed=25_100.25, spot_ts=spot_ts)

    assert row["spot_observed"] == 251_002_500
    assert row["spot_ts"] == datetime(2026, 9, 17, 9, 19, 58, tzinfo=timezone.utc)


def test_an_unobserved_spot_is_null():
    row = _row()

    assert row["spot_observed"] is None
    assert row["spot_ts"] is None


def test_the_row_matches_the_schema():
    table = pa.Table.from_pylist([_row()], schema=EVIDENCE_TICK_SCHEMA)

    assert table.num_rows == 1
    assert set(EVIDENCE_TICK_SCHEMA.names) == set(_row().keys())


def test_received_ts_is_never_null():
    assert EVIDENCE_TICK_SCHEMA.field("received_ts").nullable is False
    assert EVIDENCE_TICK_SCHEMA.field("exchange_ts").nullable is True
    assert EVIDENCE_TICK_SCHEMA.field("evidence_class").nullable is False
