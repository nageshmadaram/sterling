"""The gate's teeth.

A gate that reports a verdict and changes nothing is a document. These are the
tests that the verdict actually decides something — and, just as importantly,
that it decides the RIGHT thing: unattended execution, not the operator's own
hands.
"""
from __future__ import annotations

import pytest

from app.services import intraday_validation as val


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "t.db"))
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    db.init()
    yield


def promoted(strategy: str) -> val.StrategyValidation:
    return val.StrategyValidation(
        strategy=strategy, promoted=True, measured_at="2026-09-12",
        oos_trades=180, oos_net=42_000.0, sharpe=1.3, deflated_sharpe=0.62,
        permutation_p=0.008, max_drawdown_pct=-14.0,
        checks={"profitable": True}, reasons=[], slippage_pct=0.05,
        symbols=["NIFTY", "BANKNIFTY"])


class TestTheRecord:
    def test_an_unmeasured_strategy_appears_and_is_not_promoted(self):
        """Absent and measured-but-failed are different, and both have to be
        visible rather than one being a missing key."""
        loaded = val.load()
        assert set(loaded) == {"pivot_break", "ma_ribbon", "vwap_supertrend"}
        assert not any(v.promoted for v in loaded.values())

    def test_a_verdict_round_trips(self):
        val.record({"ma_ribbon": promoted("ma_ribbon")})
        got = val.load()["ma_ribbon"]
        assert got.promoted and got.deflated_sharpe == 0.62
        assert got.symbols == ["NIFTY", "BANKNIFTY"]

    def test_recording_one_strategy_leaves_the_others_alone(self):
        val.record({"ma_ribbon": promoted("ma_ribbon")})
        val.record({"pivot_break": val.StrategyValidation(
            strategy="pivot_break", promoted=False, measured_at="2026-09-12",
            reasons=["deflated Sharpe 0.31 < 0.5"])})
        loaded = val.load()
        assert loaded["ma_ribbon"].promoted is True
        assert loaded["pivot_break"].promoted is False

    def test_an_unreadable_record_promotes_nothing(self, monkeypatch):
        """The safe reading. A store we cannot parse must never be treated as
        evidence that something passed."""
        from app.services import db
        db.set_config("intraday_validation", "{not json at all")
        assert not any(v.promoted for v in val.load().values())


class TestWhatItPermits:
    def test_a_promoted_strategy_may_run_unattended(self):
        val.record({"ma_ribbon": promoted("ma_ribbon")})
        assert val.auto_execution_blocker("ma_ribbon") is None
        assert val.is_promoted("ma_ribbon") is True

    def test_an_unmeasured_strategy_names_the_harness(self):
        why = val.auto_execution_blocker("pivot_break")
        assert why and "walk-forward harness" in why

    def test_a_failed_strategy_repeats_the_harness_s_own_reason(self):
        """"Not promoted" tells an operator nothing. The reason tells them what
        would have to change."""
        val.record({"vwap_supertrend": val.StrategyValidation(
            strategy="vwap_supertrend", promoted=False, measured_at="2026-09-12",
            reasons=["deflated Sharpe 0.310 < 0.5 — the result does not survive "
                     "how many variants were tried"])})
        why = val.auto_execution_blocker("vwap_supertrend")
        assert why and "does not survive" in why and "2026-09-12" in why

    def test_an_unknown_strategy_is_refused_by_name(self):
        assert "not a strategy" in (val.auto_execution_blocker("made_up") or "")


class TestTheRunnerHonoursIt:
    def test_auto_entry_skips_an_unproven_strategy(self):
        from app.services import intraday_runner as runner
        assert runner.auto_blocker_for("ma_ribbon")
        val.record({"ma_ribbon": promoted("ma_ribbon")})
        assert runner.auto_blocker_for("ma_ribbon") is None

    def test_manual_arming_is_NOT_gated_on_the_harness(self):
        """An operator taking an unproven setup with their eyes open is their
        call. Refusing it would be paternalism rather than safety."""
        from app.engines.intraday import IntradayConfig
        from app.services import intraday_runner as runner
        cfg = IntradayConfig().validate()
        blocker = runner.entry_blocker("u-none", cfg, "SOMECE")
        assert blocker is None or "harness" not in blocker


def test_the_descriptor_reports_the_measurement_not_a_hardcoded_false():
    from app.engines.intraday import descriptor
    assert descriptor()["validated"] is False
    val.record({k: promoted(k) for k in
                ("pivot_break", "ma_ribbon", "vwap_supertrend")})
    d = descriptor()
    assert d["validated"] is True
    assert all(s["validated"] for s in d["strategies"])
    assert d["strategies"][0]["validation"]["deflated_sharpe"] == 0.62
