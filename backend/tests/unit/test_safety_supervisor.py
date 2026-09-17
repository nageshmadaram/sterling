"""Health composition, cross-lane exposure, and the four-level risk hierarchy.

Each of these fails closed in a different way, and each default is the one that
costs money if it is wrong the other way round.
"""
from __future__ import annotations

import pytest

from app.core.exposure import (
    Exposure,
    ExposureCoordinator,
    ExposureVerdict,
    Interaction,
    is_averaging_down,
)
from app.core.health import (
    REQUIRED_COMPONENTS,
    ComponentHealth,
    SystemHealth,
    compose_health,
    worst,
)
from app.core.risk_hierarchy import (
    INCONCLUSIVE,
    RiskConfigurationError,
    RiskHierarchy,
    RiskLevel,
)


def _all_ok(**over) -> dict[str, ComponentHealth]:
    components = {
        name: ComponentHealth(name, SystemHealth.NORMAL)
        for name in REQUIRED_COMPONENTS
    }
    components.update(over)
    return components


# ── health ────────────────────────────────────────────────────────────────


def test_all_normal_composes_to_normal():
    report = compose_health(_all_ok())
    assert report.overall is SystemHealth.NORMAL
    assert report.may_open_new_exposure is True
    assert report.missing == ()


def test_the_most_severe_component_wins():
    report = compose_health(
        _all_ok(
            safe_mode=ComponentHealth("safe_mode", SystemHealth.SAFE_MODE),
            broker=ComponentHealth("broker", SystemHealth.BROKER_ERROR),
        )
    )
    assert report.overall is SystemHealth.BROKER_ERROR


def test_recovery_required_outranks_everything():
    report = compose_health(
        _all_ok(
            broker=ComponentHealth("broker", SystemHealth.BROKER_ERROR),
            evidence=ComponentHealth("evidence", SystemHealth.EVIDENCE_ERROR),
            reconciliation=ComponentHealth(
                "reconciliation", SystemHealth.RECOVERY_REQUIRED
            ),
        )
    )
    assert report.overall is SystemHealth.RECOVERY_REQUIRED


@pytest.mark.parametrize("missing", sorted(REQUIRED_COMPONENTS))
def test_a_silent_component_is_a_failure_not_a_pass(missing):
    """"Nobody checked the broker" must not read the same as "the broker is fine"."""
    components = _all_ok()
    components.pop(missing)
    report = compose_health(components)
    assert report.overall is not SystemHealth.NORMAL
    assert missing in report.missing
    assert report.may_open_new_exposure is False


def test_an_empty_report_is_the_most_severe_state():
    report = compose_health({})
    assert report.overall is SystemHealth.RECOVERY_REQUIRED
    assert set(report.missing) == REQUIRED_COMPONENTS


def test_managing_existing_exposure_is_never_blocked():
    report = compose_health({})
    assert report.may_manage_existing_exposure is True


def test_worst_of_nothing_is_not_normal():
    assert worst() is SystemHealth.RECOVERY_REQUIRED


# ── exposure ──────────────────────────────────────────────────────────────


def _pos(lane="snapback:swing", **over) -> Exposure:
    base = dict(
        lane_key=lane,
        underlying="NIFTY",
        exchange="NFO",
        tradingsymbol="NIFTY26SEP24000CE",
        direction="long",
        quantity=75,
        expiry="2026-09-24",
        strike=24000.0,
        option_type="CE",
    )
    base.update(over)
    return Exposure(**base)


COORD = ExposureCoordinator()


def test_an_unrelated_underlying_is_allowed():
    other = _pos(
        lane="supertrend:swing",
        underlying="RELIANCE",
        tradingsymbol="RELIANCE26SEP3000CE",
    )
    assert COORD.evaluate(other, [_pos()]).allowed is True


def test_an_empty_book_allows_anything():
    assert COORD.evaluate(_pos(), []).allowed is True


def test_the_same_contract_in_the_same_direction_is_refused():
    """This is averaging down wearing a second lane's label."""
    decision = COORD.evaluate(_pos(lane="supertrend:swing"), [_pos()])
    assert decision.allowed is False
    assert decision.interaction is Interaction.SAME_CONTRACT
    assert decision.conflicting_lane == "snapback:swing"


def test_the_same_contract_in_the_opposite_direction_is_refused():
    decision = COORD.evaluate(
        _pos(lane="supertrend:swing", direction="short"), [_pos()]
    )
    assert decision.allowed is False
    assert decision.interaction is Interaction.SAME_CONTRACT


def test_same_underlying_and_expiry_same_side_is_refused():
    other = _pos(
        lane="supertrend:swing", tradingsymbol="NIFTY26SEP24500CE", strike=24500.0
    )
    decision = COORD.evaluate(other, [_pos()])
    assert decision.allowed is False
    assert decision.interaction is Interaction.SAME_EXPIRY


def test_an_undeclared_interaction_refuses_and_says_so():
    """A spread may be sensible; it has never been specified, sized or costed."""
    other = _pos(
        lane="supertrend:swing",
        tradingsymbol="NIFTY26SEP24500PE",
        strike=24500.0,
        option_type="PE",
        direction="short",
    )
    decision = COORD.evaluate(other, [_pos()])
    assert decision.allowed is False
    assert decision.reason == "EXPOSURE_INTERACTION_UNDECLARED"


def test_the_closest_conflict_is_reported_first():
    """Three conflicts; the operator needs the one they can act on."""
    far = _pos(lane="a", expiry="2026-10-29", tradingsymbol="NIFTY26OCT24000CE")
    near = _pos(lane="b")
    decision = COORD.evaluate(_pos(lane="c"), [far, near])
    assert decision.interaction is Interaction.SAME_CONTRACT
    assert decision.conflicting_lane == "b"


def test_an_exchange_difference_makes_it_a_different_contract():
    """A SENSEX option lives on BFO; identity is carried, never rebuilt."""
    bfo = _pos(
        lane="supertrend:swing",
        underlying="SENSEX",
        exchange="BFO",
        tradingsymbol="SENSEX26SEP80000CE",
    )
    assert COORD.evaluate(bfo, [_pos()]).allowed is True


def test_averaging_down_is_detected_independently():
    assert is_averaging_down(_pos(lane="other"), [_pos()]) is True
    assert is_averaging_down(_pos(lane="other", direction="short"), [_pos()]) is False
    assert is_averaging_down(_pos(), []) is False


def test_a_permissive_policy_must_be_declared_explicitly():
    """Nothing is allowed by omission; widening is an explicit act."""
    permissive = ExposureCoordinator(
        {
            (Interaction.SAME_EXPIRY, False): ExposureVerdict.ALLOW,
            (Interaction.UNRELATED, True): ExposureVerdict.ALLOW,
            (Interaction.UNRELATED, False): ExposureVerdict.ALLOW,
        }
    )
    spread = _pos(
        lane="supertrend:swing",
        tradingsymbol="NIFTY26SEP24500PE",
        strike=24500.0,
        option_type="PE",
        direction="short",
    )
    assert permissive.evaluate(spread, [_pos()]).allowed is True
    # Still refuses the relationships it did not declare.
    assert permissive.evaluate(_pos(lane="x"), [_pos()]).allowed is False


# ── risk hierarchy ────────────────────────────────────────────────────────


LANE = "snapback:swing"


def _hierarchy(**over) -> RiskHierarchy:
    limits = {
        RiskLevel.GLOBAL: {"": 100_000.0},
        RiskLevel.STRATEGY: {"snapback": 60_000.0},
        RiskLevel.MODE: {LANE: 30_000.0},
        RiskLevel.POSITION: {LANE: 10_000.0},
    }
    limits.update(over)
    return RiskHierarchy(limits=limits)


def test_a_request_inside_every_limit_is_allowed():
    assert _hierarchy().check(
        strategy_id="snapback", lane_key=LANE, requested=5_000.0
    ).allowed is True


@pytest.mark.parametrize(
    "level,used",
    [
        (RiskLevel.GLOBAL, {RiskLevel.GLOBAL: 99_000.0}),
        (RiskLevel.STRATEGY, {RiskLevel.STRATEGY: 59_000.0}),
        (RiskLevel.MODE, {RiskLevel.MODE: 29_000.0}),
        (RiskLevel.POSITION, {RiskLevel.POSITION: 9_000.0}),
    ],
)
def test_every_level_can_block_on_its_own(level, used):
    decision = _hierarchy().check(
        strategy_id="snapback", lane_key=LANE, requested=5_000.0, used=used
    )
    assert decision.allowed is False
    assert decision.level is level
    assert decision.reason == f"RISK_LIMIT_EXCEEDED_{level.value.upper()}"


def test_the_broadest_breach_is_reported_first():
    decision = _hierarchy().check(
        strategy_id="snapback",
        lane_key=LANE,
        requested=5_000.0,
        used={RiskLevel.GLOBAL: 99_000.0, RiskLevel.POSITION: 9_000.0},
    )
    assert decision.level is RiskLevel.GLOBAL


@pytest.mark.parametrize("level", list(RiskLevel))
def test_a_missing_limit_is_inconclusive_not_unlimited(level):
    """A config typo must not become an uncapped position."""
    limits = _hierarchy().limits
    pruned = {k: v for k, v in limits.items() if k is not level}
    decision = RiskHierarchy(limits=pruned).check(
        strategy_id="snapback", lane_key=LANE, requested=1.0
    )
    assert decision.allowed is False
    assert decision.reason == INCONCLUSIVE
    assert decision.level is level


def test_an_unconfigured_lane_cannot_take_risk_even_under_a_configured_strategy():
    decision = _hierarchy().check(
        strategy_id="snapback", lane_key="snapback:scalping", requested=1.0
    )
    assert decision.reason == INCONCLUSIVE
    assert decision.level is RiskLevel.MODE


def test_missing_scopes_names_exactly_what_is_needed():
    assert _hierarchy().missing_scopes(
        strategy_id="supertrend", lane_key="supertrend:swing"
    ) == ("strategy/supertrend", "mode/supertrend:swing", "position/supertrend:swing")
    assert _hierarchy().missing_scopes(strategy_id="snapback", lane_key=LANE) == ()


def test_a_limit_change_changes_the_config_hash():
    assert _hierarchy().config_hash != _hierarchy(
        **{RiskLevel.POSITION: {LANE: 12_000.0}}
    ).config_hash


def test_the_config_hash_ignores_declaration_order():
    a = RiskHierarchy(
        limits={RiskLevel.GLOBAL: {"": 1.0}, RiskLevel.MODE: {LANE: 2.0}}
    )
    b = RiskHierarchy(
        limits={RiskLevel.MODE: {LANE: 2.0}, RiskLevel.GLOBAL: {"": 1.0}}
    )
    assert a.config_hash == b.config_hash


def test_a_negative_limit_is_refused_at_construction():
    with pytest.raises(RiskConfigurationError):
        RiskHierarchy(limits={RiskLevel.GLOBAL: {"": -1.0}})


def test_negative_usage_is_refused_rather_than_creating_headroom():
    with pytest.raises(RiskConfigurationError):
        _hierarchy().check(
            strategy_id="snapback",
            lane_key=LANE,
            requested=1.0,
            used={RiskLevel.GLOBAL: -50_000.0},
        )
