"""Three lane verdicts that used to be permanently unknown.

A `None` that never changes is indistinguishable from a check nobody wrote, so
these must answer from live state — and must still answer `None` when the
question genuinely cannot be answered, because `may_send_entry` refuses on
`None` and that refusal is the safety property.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services import lane_gate_inputs
from app.services.lane_gate_inputs import (
    MIN_SHADOW_INTENTS,
    lane_identity_matches_frozen,
    lane_promotion_passed,
    shadow_gate_passed,
)

LANE = "snapback:swing"


class TestIdentityAgainstTheFrozenManifest:
    def test_no_frozen_manifest_is_unknown_not_a_match(self, monkeypatch):
        monkeypatch.setattr("app.core.release_manifest.read_manifest", lambda: None)
        assert lane_identity_matches_frozen(LANE) is None

    def test_drift_on_this_lane_fails(self, monkeypatch):
        from app.core.release_manifest import ManifestDrift, ManifestVerdict

        verdict = ManifestVerdict(
            matches=False,
            drift=(ManifestDrift(LANE, "identity_hash", "old", "new"),),
        )
        monkeypatch.setattr("app.core.release_manifest.read_manifest", lambda: {"build": {}})
        monkeypatch.setattr("app.core.release_manifest.verify_manifest", lambda stored: verdict)
        assert lane_identity_matches_frozen(LANE) is False

    def test_drift_on_another_lane_does_not_fail_this_one(self, monkeypatch):
        from app.core.release_manifest import ManifestDrift, ManifestVerdict

        verdict = ManifestVerdict(
            matches=False,
            drift=(ManifestDrift("supertrend:swing", "identity_hash", "old", "new"),),
        )
        monkeypatch.setattr("app.core.release_manifest.read_manifest", lambda: {"build": {}})
        monkeypatch.setattr("app.core.release_manifest.verify_manifest", lambda stored: verdict)
        assert lane_identity_matches_frozen(LANE) is True

    def test_a_lane_missing_from_the_running_build_fails(self, monkeypatch):
        from app.core.release_manifest import ManifestVerdict

        verdict = ManifestVerdict(matches=False, missing_lanes=(LANE,))
        monkeypatch.setattr("app.core.release_manifest.read_manifest", lambda: {"build": {}})
        monkeypatch.setattr("app.core.release_manifest.verify_manifest", lambda stored: verdict)
        assert lane_identity_matches_frozen(LANE) is False


class TestThePromotionVerdict:
    def test_an_unreadable_store_is_unknown(self, tmp_path):
        assert lane_promotion_passed(LANE, tmp_path / "nowhere.db") is None

    def test_an_empty_lane_has_not_passed(self, tmp_path):
        path = tmp_path / "obs.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE outcomes (outcome_id TEXT, authoritative INTEGER, lane_key TEXT)")
        assert lane_promotion_passed(LANE, path) is False

    def test_paper_rows_do_not_pass_the_broker_gate(self, tmp_path):
        """Real capital's question is what happened with real capital."""
        path = tmp_path / "obs.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE outcomes (outcome_id TEXT, authoritative INTEGER, "
                "lane_key TEXT, identity_hash TEXT, evidence_class TEXT, "
                "actual_total_pnl REAL, actual_costs REAL, entry_date TEXT)")
            conn.executemany(
                "INSERT INTO outcomes VALUES (?,1,?,'id',?,?,0.0,?)",
                [(f"o{i}", LANE, "paper", 500.0, f"2026-09-{(i % 28) + 1:02d}")
                 for i in range(400)])
        assert lane_promotion_passed(LANE, path) is False


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def sessions(self):
        return ["2026-09-18"] if self._rows else []

    def read(self, session_date):
        return self._rows


def _shadow_row(**overrides):
    row = {
        "lane_key": LANE,
        "session_date": "2026-09-18",
        "signal_at": "2026-09-18T04:00:00+00:00",
        "contract": "NIFTY26JAN24000CE",
        "intended_quantity": 75,
        "filled_quantity": 75,
        "fill_ratio": 1.0,
        "outcome": "FILLED",
        "evidence_class": "shadow",
        "depth_sufficient": True,
        "broker_margin": 120000.0,
        "protection_feasible": True,
        "hypothetical_fill_price": 101.0,
        "refusal_reason": None,
        "observed_at_selection": {"observed_at": "2026-09-18T04:00:00+00:00",
                                  "bid": 101.0, "ask": 102.0,
                                  "bid_qty": 900, "ask_qty": 900, "last": 101.5},
    }
    row.update(overrides)
    return row


class TestTheShadowGate:
    def test_nothing_recorded_is_unknown(self):
        assert shadow_gate_passed(LANE, store=_Store([])) is None

    def test_another_lanes_records_do_not_answer_this_lane(self):
        rows = [_shadow_row(lane_key="supertrend:swing")]
        assert shadow_gate_passed(LANE, store=_Store(rows)) is None

    def test_too_few_intents_is_not_acceptable(self):
        rows = [_shadow_row() for _ in range(10)]
        assert shadow_gate_passed(LANE, store=_Store(rows)) is False

    def test_an_unknown_margin_fails_however_many_intents(self):
        rows = [_shadow_row() for _ in range(MIN_SHADOW_INTENTS)]
        rows[0] = _shadow_row(broker_margin=None)
        assert shadow_gate_passed(LANE, store=_Store(rows)) is False

    def test_a_full_clean_sample_passes(self):
        rows = [_shadow_row() for _ in range(MIN_SHADOW_INTENTS)]
        assert shadow_gate_passed(LANE, store=_Store(rows)) is True


class TestTheReportUsesThem:
    def test_the_permission_report_no_longer_hardcodes_unknown(self, monkeypatch):
        from app.services import capital_permission_report as report

        seen: dict[str, object] = {}

        monkeypatch.setattr(lane_gate_inputs, "lane_promotion_passed",
                            lambda key, *a, **k: seen.setdefault("promotion", key) and True)
        monkeypatch.setattr(lane_gate_inputs, "lane_identity_matches_frozen",
                            lambda key: seen.setdefault("identity", key) and True)
        monkeypatch.setattr(lane_gate_inputs, "shadow_gate_passed",
                            lambda key, **k: seen.setdefault("shadow", key) and True)

        report.lane_permission(LANE)
        assert seen == {"promotion": LANE, "identity": LANE, "shadow": LANE}
