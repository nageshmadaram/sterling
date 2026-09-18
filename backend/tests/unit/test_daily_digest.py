"""A digest on a quiet day, so silence means the scheduler died.

The rules: it is always produced, unknowns are printed rather than omitted, and
the next action is the one an operator should actually take first.
"""
from __future__ import annotations

import pytest

from app.services.daily_digest import UNKNOWN, Digest, build_digest, render_digest


def _digest(**overrides) -> Digest:
    base = Digest(
        generated_at="2026-09-18T08:00:00+00:00",
        release_tag="sterling-family-runtime-1.0", runtime_sha="49cc66ee73bd",
        health="PASS", broker="ok: AA0595", account_binding="ok",
        safety="ok: NORMAL", recovery="ok: CLEAN",
        open_exposure="0", unresolved_exposure="0",
        market_feed="ok: last tick 1s ago", backup_age="ok: 2h old",
        lanes=["snapback:swing: paper"],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    base.next_action = __import__(
        "app.services.daily_digest", fromlist=["_next_action"]
    )._next_action(base)
    return base


class TestItAlwaysProducesSomething:
    def test_a_digest_is_built_even_when_everything_is_unreadable(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("nothing works today")

        monkeypatch.setattr("app.services.snapback_preflight.run_preflight", _boom)
        monkeypatch.setattr("app.services.exposure_snapshot.exposure_snapshot", _boom)
        digest = build_digest()
        assert digest.generated_at
        assert UNKNOWN in digest.health

    def test_every_field_appears_in_the_rendering(self):
        text = render_digest(_digest())
        for label in ("Release:", "Health:", "Broker:", "Account binding:",
                      "Safety:", "Exposure:", "Market feed:", "Backup:",
                      "Lanes:", "Next safe action:"):
            assert label in text

    def test_an_unknown_is_printed_rather_than_omitted(self):
        # A missing line reads as "fine" to a tired person at 6am.
        text = render_digest(_digest(market_feed=UNKNOWN))
        assert f"Market feed: {UNKNOWN}" in text


class TestTheNextAction:
    def test_recovery_comes_before_everything(self):
        digest = _digest(recovery="FAIL: recovery state is RECOVERY_REQUIRED",
                         safety="FAIL: SAFE_MODE is engaged")
        assert "reconcile" in digest.next_action

    def test_safe_mode_is_named_with_its_command(self):
        digest = _digest(safety="FAIL: SAFE_MODE is engaged: stale feed")
        assert "safe off --ack" in digest.next_action

    def test_open_exposure_outranks_a_stale_backup(self):
        digest = _digest(open_exposure="2", backup_age="FAIL: 9 days old")
        assert "open exposure" in digest.next_action

    def test_a_broker_logout_sends_the_operator_to_the_runbook(self):
        digest = _digest(broker=f"{UNKNOWN}: no session")
        assert "BROKER_REAUTH" in digest.next_action

    def test_a_healthy_quiet_day_says_do_nothing(self):
        assert _digest().next_action == "Nothing. The system is collecting evidence."


class TestItAgreesWithTheDoctor:
    def test_health_is_blocked_when_a_check_could_not_run(self, monkeypatch):
        from app.core.operator_report import DoctorCheck

        class _Report:
            checks = (DoctorCheck("clock", None, "could not read"),)
            failures = ()
            unknowns = (DoctorCheck("clock", None, "could not read"),)

        monkeypatch.setattr("app.core.operator_report.doctor_from_preflight",
                            lambda *a, **k: _Report())
        digest = build_digest()
        assert digest.health.startswith("BLOCKED")
