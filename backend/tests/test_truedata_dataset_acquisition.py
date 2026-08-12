from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[2] / "scripts" / "adaptive_edge"
SCRIPT = SCRIPT_DIR / "acquire_truedata_research_dataset.py"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("truedata_dataset_acquisition", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def bars() -> list[dict[str, str]]:
    return [
        {
            "timestamp": "2026-08-12T09:15:00",
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100.5",
            "volume": "20",
            "oi": "3",
        },
        {
            "timestamp": "2026-08-12T09:16:00",
            "open": "100.5",
            "high": "102",
            "low": "100",
            "close": "101.5",
            "volume": "30",
            "oi": "4",
        },
    ]


def test_dataset_version_is_deterministic():
    assert MODULE.dataset_version("NIFTY-I", "2026-01-01", "2026-08-01") == (
        "ae21-truedata-nifty-i-2026-01-01-2026-08-01-v1"
    )


def test_canonical_csv_has_fixed_field_order(tmp_path: Path):
    path = tmp_path / "dataset.csv"
    MODULE.write_canonical_csv(path, bars())
    assert path.read_text(encoding="utf-8") == (
        "timestamp,open,high,low,close,volume,oi\n"
        "2026-08-12T09:15:00,100,101,99,100.5,20,3\n"
        "2026-08-12T09:16:00,100.5,102,100,101.5,30,4\n"
    )


def test_sha256_is_content_addressed(tmp_path: Path):
    path = tmp_path / "dataset.csv"
    path.write_text("abc\n", encoding="utf-8")
    assert MODULE.sha256(path) == (
        "edeaaff3f1774ad2888673770c6d64097e391bc362d7d6fb34982ddf0efd18cb"
    )


def test_missingness_summary_counts_empty_values():
    rows = bars()
    rows[1]["volume"] = ""
    assert MODULE.missingness_summary(rows)["volume"] == 1
    assert MODULE.missingness_summary(rows)["close"] == 0


def test_manifest_marks_provider_availability_semantics_unverified():
    manifest = MODULE.build_manifest(
        dataset_version_value="dataset-v1",
        symbol="NIFTY-I",
        start="2026-01-01",
        end="2026-08-01",
        first_timestamp="2026-08-12T09:15:00",
        last_timestamp="2026-08-12T09:16:00",
        source_timezone="Asia/Kolkata",
        session_calendar_version="NSE-EQUITY-2026-v1",
        feature_set_version="REQUIRED",
        label_definition_version="A26-ND-v1",
        source_hash="abc123",
        row_count=2,
        missingness={field: 0 for field in MODULE.CANONICAL_FIELDS},
    )
    assert manifest["entitlement_verified"] is True
    assert manifest["availability_semantics_verified"] is False
    assert manifest["source_file_sha256"] == "abc123"
