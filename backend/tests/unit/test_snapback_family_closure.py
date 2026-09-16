"""1.5 closure: operations, product scope, and succession.

Four remaining items, all of which decide what a non-technical operator can see
and do, and what happens to Sterling when its operator is gone.
"""

from __future__ import annotations

import pytest


# ------------------------------------------- promotion record must persist


def test_a_promotion_record_that_cannot_be_stored_is_not_a_verdict(tmp_path):
    """A verdict nobody can audit later is not evidence of anything. If the
    record cannot be written, the package must not claim a decision was made."""
    from app.services.snapback_promotion import PromotionService

    class _Warehouse:
        def record_promotion_record(self, **kwargs):
            raise RuntimeError("disk full")

        def get_all_records(self):
            return {}

    service = PromotionService()
    result = service._record(_Warehouse(), _verdict())

    assert result.verdict == "INCONCLUSIVE"
    assert any("promotion_record" in r for r in result.missing_requirements)


def test_a_stored_record_keeps_its_verdict(tmp_path):
    from app.services.snapback_promotion import PromotionService

    stored = {}

    class _Warehouse:
        def record_promotion_record(self, **kwargs):
            stored.update(kwargs)

    result = PromotionService()._record(_Warehouse(), _verdict())

    assert result.verdict == "FAILED"
    assert stored


def _verdict():
    from app.services.snapback_promotion import PromotionResult

    return PromotionResult(
        promotion_id="PROMO-1", experiment_id="E1", gate_version="v1",
        gate_input_hash="h", source_snapshot_sha256="s",
        runtime_build_sha="b", config_hash="c", rule_hash="r",
        execution_policy_hash="p", cost_schedule_hash="cs",
        observed_sessions=1, completed_trades=1, verdict="FAILED",
        promoted=False, data_quality_ok=True,
    )


# ------------------------------------- family mode cannot weaken preflight


@pytest.mark.parametrize("env", [
    "STERLING_SKIP_CLOCK_CHECK",
    "STERLING_ALLOW_DIRTY_WORKTREE",
])
def test_family_mode_ignores_preflight_escape_hatches(env, monkeypatch):
    """These exist for a developer machine. On the family deployment they would
    let a mis-set clock or uncommitted code produce evidence that cannot be
    reproduced.

    Asserted on the hatch itself rather than on the check's result: whether a
    worktree happens to be dirty is a property of the machine running the
    tests, not of the contract.
    """
    from app.services.snapback_preflight import _escape_hatch_allowed

    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    monkeypatch.setenv(env, "true")

    assert _escape_hatch_allowed(env) is False


@pytest.mark.parametrize("env", [
    "STERLING_SKIP_CLOCK_CHECK",
    "STERLING_ALLOW_DIRTY_WORKTREE",
])
def test_the_hatches_are_available_on_a_workstation(env, monkeypatch):
    from app.services.snapback_preflight import _escape_hatch_allowed

    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)
    monkeypatch.setenv(env, "true")

    assert _escape_hatch_allowed(env) is True


def test_an_unsynchronised_clock_is_refused_under_family_mode(monkeypatch):
    """The consequence that matters, for the check whose result does not depend
    on the machine's git state."""
    from app.services import snapback_preflight

    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    monkeypatch.setenv("STERLING_SKIP_CLOCK_CHECK", "true")
    monkeypatch.setattr(
        snapback_preflight, "_ntp_synchronised", lambda: (False, "no NTP"),
    )

    passed, _ = snapback_preflight.check_clock()

    assert passed is False


def test_the_escape_hatches_still_work_outside_family_mode(monkeypatch):
    from app.services import snapback_preflight

    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)
    monkeypatch.setenv("STERLING_SKIP_CLOCK_CHECK", "true")
    monkeypatch.setattr(
        snapback_preflight, "_ntp_synchronised", lambda: (False, "no NTP"),
    )

    passed, _ = snapback_preflight.check_clock()

    assert passed is True


# --------------------------------- family mode shows only validated strategies


def test_only_a_promoted_strategy_is_offered_to_the_family():
    from app.services.snapback_family_mode import family_visible_strategies

    visible = family_visible_strategies()

    assert visible == {"snapback"}


@pytest.mark.parametrize("strategy", [
    "gamma_move", "adaptive_edge", "pivot_break", "ma_ribbon",
    "vwap_supertrend", "nifty_orb", "oi_wall_flow", "atm_premium_imbalance",
    "navigator",
])
def test_an_unvalidated_strategy_cannot_be_armed_by_the_family(strategy, monkeypatch):
    """Manual arming of an unvalidated strategy is defensible on a research
    workstation. On the family product it is a way to lose money on a strategy
    nobody has shown works."""
    from app.services.snapback_family_mode import guard_family_strategy

    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    with pytest.raises(PermissionError):
        guard_family_strategy(strategy)


def test_snapback_is_permitted(monkeypatch):
    from app.services.snapback_family_mode import guard_family_strategy

    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    guard_family_strategy("snapback")


def test_outside_family_mode_every_strategy_is_available(monkeypatch):
    from app.services.snapback_family_mode import guard_family_strategy

    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)

    guard_family_strategy("gamma_move")


# ------------------------------------------------------------- succession


def test_a_rebind_halts_exposure_first(tmp_path):
    """Nothing about a new account may be touched while the old one still holds
    positions."""
    from app.services.snapback_succession import SuccessionRefused, rebind_family_account

    with pytest.raises(SuccessionRefused) as exc:
        rebind_family_account(
            new_user_id="heir", new_account_id="KITE-HEIR",
            open_positions=3, unresolved_orders=0, confirmed_by="heir",
        )

    assert "exposure" in str(exc.value).lower()


def test_a_rebind_refuses_while_orders_are_unresolved(tmp_path):
    from app.services.snapback_succession import SuccessionRefused, rebind_family_account

    with pytest.raises(SuccessionRefused):
        rebind_family_account(
            new_user_id="heir", new_account_id="KITE-HEIR",
            open_positions=0, unresolved_orders=2, confirmed_by="heir",
        )


def test_a_clean_rebind_produces_the_new_binding(tmp_path):
    from app.services.snapback_succession import rebind_family_account

    plan = rebind_family_account(
        new_user_id="heir", new_account_id="KITE-HEIR",
        open_positions=0, unresolved_orders=0, confirmed_by="heir",
    )

    assert plan.new_account_id == "KITE-HEIR"
    assert plan.requires_fresh_broker_authorization is True
    assert plan.evidence_preserved_read_only is True


def test_a_rebind_never_reuses_the_previous_credentials():
    """Transacting in a deceased person's account is not legal, and a nominee
    must claim and transfer to their own account. Sterling must not make
    carrying on with the old login look like an option."""
    from app.services.snapback_succession import rebind_family_account

    plan = rebind_family_account(
        new_user_id="heir", new_account_id="KITE-HEIR",
        open_positions=0, unresolved_orders=0, confirmed_by="heir",
    )

    assert plan.reuses_previous_credentials is False
    assert "own account" in plan.operator_instructions.lower()


def test_research_evidence_survives_a_rebind():
    """The strategy's history belongs to Sterling. Broker inventory belongs to
    the legal account. Mixing them would either destroy the research record or
    attribute somebody else's positions to it."""
    from app.services.snapback_succession import rebind_family_account

    plan = rebind_family_account(
        new_user_id="heir", new_account_id="KITE-HEIR",
        open_positions=0, unresolved_orders=0, confirmed_by="heir",
    )

    assert plan.evidence_preserved_read_only is True
    assert plan.account_state_reset is True


def test_a_rebind_requires_an_explicit_human_confirmation():
    from app.services.snapback_succession import SuccessionRefused, rebind_family_account

    with pytest.raises(SuccessionRefused):
        rebind_family_account(
            new_user_id="heir", new_account_id="KITE-HEIR",
            open_positions=0, unresolved_orders=0, confirmed_by="",
        )


def test_the_new_account_must_differ_from_the_old(monkeypatch):
    from app.services.snapback_succession import SuccessionRefused, rebind_family_account

    monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", "KITE-SAME")

    with pytest.raises(SuccessionRefused):
        rebind_family_account(
            new_user_id="heir", new_account_id="KITE-SAME",
            open_positions=0, unresolved_orders=0, confirmed_by="heir",
        )
