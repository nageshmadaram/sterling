"""The first real order: exactly one lot, inside a configured envelope."""
from __future__ import annotations

import pytest

from app.core.live_minimum import (
    DAILY_LOSS_ENV,
    GROSS_EXPOSURE_ENV,
    LiveMinimumEnvelope,
    check_order,
    configured_envelope,
)

ENVELOPE = LiveMinimumEnvelope(daily_loss_budget=2_000.0, gross_exposure_limit=50_000.0)


#: Section 21.1 adds the system-state inputs. A fully-supplied order is the
#: only one that can pass; the fixture supplies them so each test can withdraw
#: exactly one and see it refuse.
def _order(**overrides):
    order = dict(
        quantity=75, minimum_executable_quantity=75, order_value=7_500.0,
        open_gross_exposure=0.0, realised_loss_today=0.0, is_averaging_down=False,
        envelope=ENVELOPE,
        lane_permission=True, release_certification=True, account_binding=True,
        safety_state=True, recovery_state=True, broker_session=True,
        market_freshness=True, unresolved_exposure=0, daily_loss_remaining=2_000.0,
    )
    order.update(overrides)
    return order


class TestConfiguration:
    def test_an_unconfigured_envelope_permits_nothing(self, monkeypatch):
        monkeypatch.delenv(DAILY_LOSS_ENV, raising=False)
        monkeypatch.delenv(GROSS_EXPOSURE_ENV, raising=False)
        limits = configured_envelope()
        assert limits.configured is False
        verdict = check_order(**{**_order(), "envelope": limits})
        assert "LIVE_MINIMUM_NOT_CONFIGURED" in verdict.blockers

    def test_a_zero_or_negative_budget_is_not_a_budget(self):
        limits = configured_envelope({DAILY_LOSS_ENV: "0", GROSS_EXPOSURE_ENV: "-5"})
        assert limits.daily_loss_budget is None
        assert limits.gross_exposure_limit is None

    def test_a_configured_envelope_reads_both_limits(self):
        limits = configured_envelope({DAILY_LOSS_ENV: "2000", GROSS_EXPOSURE_ENV: "50000"})
        assert limits.configured is True


class TestTheEnvelope:
    def test_exactly_the_minimum_lot_is_permitted(self):
        assert check_order(**_order()).allowed is True

    def test_more_than_the_minimum_is_a_different_experiment(self):
        verdict = check_order(**_order(quantity=150))
        assert verdict.allowed is False
        assert "QUANTITY_ABOVE_MINIMUM" in verdict.blockers

    def test_exceeding_gross_exposure_refuses(self):
        verdict = check_order(**_order(open_gross_exposure=45_000.0))
        assert "GROSS_EXPOSURE_EXCEEDED" in verdict.blockers

    def test_a_spent_daily_budget_refuses(self):
        verdict = check_order(**_order(realised_loss_today=2_000.0))
        assert "DAILY_LOSS_BUDGET_SPENT" in verdict.blockers

    def test_averaging_down_is_prohibited(self):
        verdict = check_order(**_order(is_averaging_down=True))
        assert "AVERAGING_DOWN_PROHIBITED" in verdict.blockers


class TestUnknownIsNeverSmall:
    @pytest.mark.parametrize("field,code", [
        ("minimum_executable_quantity", "MINIMUM_QUANTITY_UNKNOWN"),
        ("order_value", "ORDER_VALUE_UNKNOWN"),
        ("open_gross_exposure", "GROSS_EXPOSURE_UNKNOWN"),
        ("realised_loss_today", "DAILY_LOSS_UNKNOWN"),
        ("is_averaging_down", "AVERAGING_DOWN_UNKNOWN"),
    ])
    def test_every_unreadable_input_refuses(self, field, code):
        verdict = check_order(**_order(**{field: None}))
        assert verdict.allowed is False
        assert code in verdict.blockers

    def test_every_blocker_is_reported_not_just_the_first(self):
        verdict = check_order(**_order(quantity=150, realised_loss_today=None,
                                       is_averaging_down=True))
        assert {"QUANTITY_ABOVE_MINIMUM", "DAILY_LOSS_UNKNOWN",
                "AVERAGING_DOWN_PROHIBITED"} <= set(verdict.blockers)


class TestTheSystemStateInputs:
    """Section 21.1: the order is only as safe as the machine sending it."""

    @pytest.mark.parametrize("field,code", [
        ("lane_permission", "LANE_NOT_PERMITTED"),
        ("release_certification", "RELEASE_NOT_CERTIFIED"),
        ("account_binding", "ACCOUNT_BINDING_NOT_READY"),
        ("safety_state", "SAFETY_BLOCKS_EXPOSURE"),
        ("recovery_state", "RECOVERY_REQUIRED"),
        ("broker_session", "BROKER_SESSION_NOT_READY"),
        ("market_freshness", "MARKET_DATA_STALE"),
    ])
    def test_a_false_state_refuses_with_its_own_code(self, field, code):
        verdict = check_order(**_order(**{field: False}))
        assert code in verdict.blockers

    @pytest.mark.parametrize("field,code", [
        ("lane_permission", "LANE_PERMISSION_UNKNOWN"),
        ("release_certification", "RELEASE_CERTIFICATION_UNKNOWN"),
        ("account_binding", "ACCOUNT_BINDING_UNKNOWN"),
        ("safety_state", "SAFETY_STATE_UNKNOWN"),
        ("recovery_state", "RECOVERY_STATE_UNKNOWN"),
        ("broker_session", "BROKER_SESSION_UNKNOWN"),
        ("market_freshness", "MARKET_FRESHNESS_UNKNOWN"),
        ("unresolved_exposure", "UNRESOLVED_EXPOSURE_UNKNOWN"),
        ("daily_loss_remaining", "DAILY_LOSS_REMAINING_UNKNOWN"),
    ])
    def test_an_unreadable_state_refuses_distinctly_from_a_no(self, field, code):
        verdict = check_order(**_order(**{field: None}))
        assert code in verdict.blockers

    def test_open_unresolved_exposure_refuses(self):
        assert "UNRESOLVED_EXPOSURE_OPEN" in check_order(
            **_order(unresolved_exposure=1)).blockers

    def test_a_spent_remaining_budget_refuses(self):
        assert "DAILY_LOSS_BUDGET_SPENT" in check_order(
            **_order(daily_loss_remaining=0.0)).blockers

    def test_the_defaults_refuse_everything_that_was_not_supplied(self):
        # A caller that forgets the system state gets a refusal per omission,
        # never an order.
        verdict = check_order(
            quantity=75, minimum_executable_quantity=75, order_value=1.0,
            open_gross_exposure=0.0, realised_loss_today=0.0,
            is_averaging_down=False, envelope=ENVELOPE)
        assert verdict.allowed is False
        assert len(verdict.blockers) == 9
