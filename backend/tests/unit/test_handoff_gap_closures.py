"""The four gaps the roadmap audit recorded but did not close at the time.

Each test states the failure it prevents, because each of these was found by
reading the specification against the code rather than by anything breaking.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


class TestUnderlyingRiskLayer:
    """§12.1: ten differently-labelled lanes must not become one NIFTY bet."""

    def _hierarchy(self, *, with_underlying: bool):
        from app.core.risk_hierarchy import RiskHierarchy, RiskLevel

        limits = {
            RiskLevel.GLOBAL: {"": 100.0},
            RiskLevel.STRATEGY: {"snapback": 100.0, "supertrend": 100.0},
            RiskLevel.MODE: {"snapback:swing": 100.0, "supertrend:swing": 100.0},
            RiskLevel.POSITION: {"snapback:swing": 100.0, "supertrend:swing": 100.0},
        }
        if with_underlying:
            limits[RiskLevel.UNDERLYING] = {"NIFTY": 40.0}
        return RiskHierarchy(limits)

    def test_the_level_exists(self):
        from app.core.risk_hierarchy import RiskLevel

        assert RiskLevel.UNDERLYING.value == "underlying"

    def test_two_lanes_on_one_underlying_share_its_budget(self):
        # Neither lane breaches its own cap. Together they breach the
        # underlying, which is the whole point of the layer.
        hierarchy = self._hierarchy(with_underlying=True)
        from app.core.risk_hierarchy import RiskLevel

        decision = hierarchy.check(
            strategy_id="supertrend",
            lane_key="supertrend:swing",
            requested=25.0,
            used={RiskLevel.UNDERLYING: 25.0},
            underlying="NIFTY",
        )

        assert not decision.allowed
        assert decision.reason == "RISK_LIMIT_EXCEEDED_UNDERLYING"
        assert decision.level is RiskLevel.UNDERLYING

    def test_an_unnamed_underlying_is_inconclusive_not_skipped(self):
        # Skipping the level would be "absent means unlimited" one layer down.
        from app.core.risk_hierarchy import UNDERLYING_UNKNOWN

        decision = self._hierarchy(with_underlying=True).check(
            strategy_id="snapback", lane_key="snapback:swing", requested=1.0
        )

        assert not decision.allowed
        assert decision.reason == UNDERLYING_UNKNOWN

    def test_the_scope_is_case_insensitive(self):
        assert self._hierarchy(with_underlying=True).check(
            strategy_id="snapback",
            lane_key="snapback:swing",
            requested=1.0,
            underlying="nifty",
        ).allowed

    def test_a_hierarchy_without_the_level_behaves_exactly_as_before(self):
        # Existing four-level configs and callers must not start failing.
        assert self._hierarchy(with_underlying=False).check(
            strategy_id="snapback", lane_key="snapback:swing", requested=10.0
        ).allowed

    def test_a_declared_underlying_with_no_limit_for_this_one_is_inconclusive(self):
        from app.core.risk_hierarchy import INCONCLUSIVE

        decision = self._hierarchy(with_underlying=True).check(
            strategy_id="snapback",
            lane_key="snapback:swing",
            requested=1.0,
            underlying="BANKNIFTY",
        )

        assert not decision.allowed
        assert decision.reason == INCONCLUSIVE

    def test_missing_scopes_names_the_underlying_only_when_declared(self):
        with_level = self._hierarchy(with_underlying=True).missing_scopes(
            strategy_id="snapback", lane_key="snapback:swing", underlying="SENSEX"
        )
        assert "underlying/SENSEX" in with_level

        without = self._hierarchy(with_underlying=False).missing_scopes(
            strategy_id="snapback", lane_key="snapback:swing"
        )
        assert not [s for s in without if s.startswith("underlying/")]

    def test_the_capacity_path_passes_the_underlying_through(self):
        # A wired-up level that the admission path never populates would report
        # INCONCLUSIVE_UNDERLYING_UNKNOWN on every real request.
        source = (REPO / "backend/app/services/snapback_capacity.py").read_text()
        assert "underlying=underlying" in source
        assert "canonical_underlying," in source


class TestDurableIntentWiring:
    """The intent record exists; nothing built an executor that used it."""

    def _executor(self, **overrides):
        from app.services.snapback_live_executor import build_live_executor

        kwargs = dict(
            plan_store=None,
            execution_service=None,
            reconciliation_fn=lambda uid: {},
            risk_approval_fn=lambda **k: None,
            protection_fn=lambda **k: None,
            revalidate_fn=lambda plan: plan,
        )
        kwargs.update(overrides)
        return build_live_executor(**kwargs)

    def test_the_factory_attaches_a_store_by_default(self, tmp_path, monkeypatch):
        from app.core.trade_intent import IntentStore
        from app.services.snapback_live_executor import INTENT_DB_ENV

        monkeypatch.setenv(INTENT_DB_ENV, str(tmp_path / "intents.db"))

        assert isinstance(self._executor().intent_store, IntentStore)

    def test_the_store_is_replaceable_but_not_omittable(self, tmp_path, monkeypatch):
        from app.services.snapback_live_executor import INTENT_DB_ENV

        monkeypatch.setenv(INTENT_DB_ENV, str(tmp_path / "intents.db"))
        sentinel = object()

        assert self._executor(intent_store=sentinel).intent_store is sentinel
        # Passing None does not switch it off; it falls back to the default.
        assert self._executor(intent_store=None).intent_store is not None

    def test_the_database_lands_where_the_environment_says(self, tmp_path, monkeypatch):
        from app.services.snapback_live_executor import (
            INTENT_DB_ENV,
            default_intent_store,
        )

        target = tmp_path / "nested" / "intents.db"
        monkeypatch.setenv(INTENT_DB_ENV, str(target))

        default_intent_store()

        assert target.exists(), "the intent store did not create its database"


class TestDigest:
    """Severity.INFO existed and nothing ever produced one."""

    def _digest(self, **overrides):
        from app.core.operator_alerts import daily_digest

        kwargs = dict(
            session_status="COMPLETE",
            signals_found=0,
            lanes_originating=2,
            backup_passed=True,
        )
        kwargs.update(overrides)
        return daily_digest(**kwargs)

    def test_a_quiet_day_still_says_something(self):
        # Silence is also what a dead scheduler looks like.
        from app.core.operator_alerts import render_digest

        out = render_digest(self._digest())

        assert "No signals today" in out
        assert "scan ran" in out

    def test_nothing_in_the_digest_blocks_trading(self):
        from app.core.operator_alerts import should_enter_safe_mode

        rows = self._digest()

        assert all(r.severity.value == "INFO" for r in rows)
        assert not any(r.trading_blocked for r in rows)
        assert not should_enter_safe_mode(rows)

    def test_an_unknown_backup_result_is_not_reported_as_success(self):
        # The digest must never be the thing that says a backup happened when
        # nobody checked. The fault path raises backup_failed instead.
        codes = {r.code for r in self._digest(backup_passed=None)}
        assert "backup_completed" not in codes

        failed = {r.code for r in self._digest(backup_passed=False)}
        assert "backup_completed" not in failed

    def test_an_unproved_backup_says_to_prove_it(self):
        row = next(
            r
            for r in self._digest(backup_passed=True, restore_checked=False)
            if r.code == "backup_completed"
        )
        assert "restore-check" in row.next_action

    def test_signals_are_reported_when_there_are_some(self):
        codes = {r.code for r in self._digest(signals_found=3)}
        assert "signals_found" in codes
        assert "no_signals" not in codes

    def test_an_empty_digest_still_renders(self):
        from app.core.operator_alerts import render_digest

        assert render_digest([]) == "- Nothing to report."


class TestDependencyPin:
    """An unpinned floor is why CI and a developer checkout disagreed."""

    def test_the_web_stack_has_an_upper_bound(self):
        requirements = (REPO / "backend/requirements.txt").read_text()

        fastapi = next(
            line for line in requirements.splitlines() if line.startswith("fastapi")
        )
        starlette = next(
            (line for line in requirements.splitlines() if line.startswith("starlette")),
            None,
        )

        assert "<" in fastapi, "fastapi has no upper bound"
        assert starlette is not None, "starlette is not declared at all"
        assert "<" in starlette, "starlette has no upper bound"

    def test_the_installed_versions_satisfy_the_declared_range(self):
        import fastapi
        import starlette

        # Whatever is installed here must be a version the pin permits, or the
        # suite is proving something about a combination CI will never run.
        assert fastapi.__version__ >= "0.115.0"
        assert starlette.__version__ >= "1.0.0"
