from pathlib import Path
import importlib.util

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts/adaptive_edge/validate_truedata_research_source.py"
SPEC = importlib.util.spec_from_file_location("truedata_research_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(timestamp: str) -> dict[str, str]:
    return {
        "timestamp": timestamp,
        "open": "100",
        "high": "101",
        "low": "99",
        "close": "100.5",
        "volume": "20",
        "oi": "3",
    }


def test_validate_rows_accepts_ordered_complete_population():
    bars = [
        row("2026-08-12T09:15:00"),
        row("2026-08-12T09:16:00"),
    ]
    assert MODULE.validate_rows(bars) == (
        2,
        "2026-08-12T09:15:00",
        "2026-08-12T09:16:00",
    )


def test_validate_rows_rejects_duplicate_timestamps():
    bars = [row("2026-08-12T09:15:00"), row("2026-08-12T09:15:00")]
    with pytest.raises(ValueError, match="duplicate timestamps"):
        MODULE.validate_rows(bars)


def test_validate_rows_rejects_out_of_order_timestamps():
    bars = [row("2026-08-12T09:16:00"), row("2026-08-12T09:15:00")]
    with pytest.raises(ValueError, match="monotonically increasing"):
        MODULE.validate_rows(bars)


def test_validate_rows_rejects_mixed_timestamp_timezone_awareness():
    bars = [
        row("2026-08-12T09:15:00"),
        row("2026-08-12T09:16:00+05:30"),
    ]
    with pytest.raises(ValueError, match="mixed timestamp timezone awareness"):
        MODULE.validate_rows(bars)


def test_validate_rows_rejects_missing_canonical_fields():
    bars = [row("2026-08-12T09:15:00")]
    del bars[0]["oi"]
    with pytest.raises(ValueError, match="missing canonical bar fields"):
        MODULE.validate_rows(bars)


def test_validate_rows_rejects_malformed_timestamp():
    bars = [row("not-a-timestamp")]
    with pytest.raises(ValueError, match="invalid provider timestamp"):
        MODULE.validate_rows(bars)


def test_validate_rows_rejects_empty_population():
    with pytest.raises(ValueError, match="no 1-minute bars"):
        MODULE.validate_rows([])
