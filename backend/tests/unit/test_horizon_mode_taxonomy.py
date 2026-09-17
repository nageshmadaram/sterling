"""Mode taxonomy, focus policy and lane identity.

These pin the rules that stop two different experiments pooling into one
sample: canonical names, refused aliases, and an identity that changes when
any rule changes.
"""
from __future__ import annotations

import pytest

from app.core.focus import (
    DEFAULT_FOCUS,
    FOCUSABLE,
    FocusConfigurationError,
    parse_focus,
)
from app.core.horizon import (
    MODE_TIMELINES,
    AmbiguousLegacyMode,
    HorizonMode,
    UnknownMode,
    canonical_mode,
    legacy_mode_of,
    session_bound_modes,
    timeline_for,
)
from app.core.strategy_identity import (
    IdentityError,
    StrategyModeIdentity,
    build_identity,
)

RUNTIME = "0123456789abcdef0123456789abcdef01234567"
TAG = "snapback-prospective-runtime-1.6"


# ── taxonomy ──────────────────────────────────────────────────────────────


def test_exactly_five_canonical_modes():
    assert [m.value for m in HorizonMode] == [
        "ultra_scalping",
        "scalping",
        "intraday",
        "overnight",
        "swing",
    ]


def test_every_canonical_mode_has_a_timeline():
    assert set(MODE_TIMELINES) == set(HorizonMode)


@pytest.mark.parametrize("mode", list(HorizonMode))
def test_canonical_value_round_trips(mode):
    assert canonical_mode(mode.value) is mode
    assert canonical_mode(mode) is mode


def test_snapback_legacy_scalp_maps_to_scalping():
    assert canonical_mode("scalp") is HorizonMode.SCALPING


def test_legacy_spelling_is_preserved_for_traceability():
    assert legacy_mode_of("scalp") == "scalp"
    # Already canonical: nothing worth storing.
    assert legacy_mode_of("scalping") is None


def test_positional_is_never_silently_migrated():
    """``positional`` predates the overnight/swing split.

    Mapping it either way would invent the very distinction the five-mode
    model exists to measure, so it must refuse.
    """
    for spelling in ("positional", "position", "POSITIONAL"):
        with pytest.raises(AmbiguousLegacyMode):
            canonical_mode(spelling)


def test_unknown_mode_is_refused_not_defaulted():
    with pytest.raises(UnknownMode):
        canonical_mode("moon_shot")
    with pytest.raises(UnknownMode):
        canonical_mode("")


# ── timelines ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mode,expected_max,hard,unit",
    [
        (HorizonMode.ULTRA_SCALPING, 10 * 60, 20 * 60, "seconds"),
        (HorizonMode.SCALPING, 45 * 60, 90 * 60, "seconds"),
        (HorizonMode.INTRADAY, 4 * 3600, 6 * 3600 + 5 * 60, "seconds"),
        (HorizonMode.OVERNIGHT, 3, 5, "trading_sessions"),
        (HorizonMode.SWING, 10, 15, "trading_sessions"),
    ],
)
def test_declared_time_budgets(mode, expected_max, hard, unit):
    cfg = timeline_for(mode)
    assert cfg.expected_hold_max == expected_max
    assert cfg.hard_hold_limit == hard
    assert cfg.duration_unit == unit


def test_session_bound_modes_cannot_carry_overnight_risk():
    bound = session_bound_modes()
    assert bound == {
        HorizonMode.ULTRA_SCALPING,
        HorizonMode.SCALPING,
        HorizonMode.INTRADAY,
    }
    for mode in bound:
        cfg = timeline_for(mode)
        assert cfg.allow_overnight is False
        # A mode that may not hold overnight must name what forces it flat.
        assert cfg.force_close_time == "15:20"


def test_overnight_and_swing_are_measured_in_sessions_not_days():
    for mode in (HorizonMode.OVERNIGHT, HorizonMode.SWING):
        assert timeline_for(mode).duration_unit == "trading_sessions"


def test_overnight_and_swing_are_distinct_budgets():
    """The whole reason ``positional`` cannot be auto-migrated."""
    assert timeline_for(HorizonMode.OVERNIGHT).hard_hold_limit == 5
    assert timeline_for(HorizonMode.SWING).hard_hold_limit == 15


# ── focus ─────────────────────────────────────────────────────────────────


def test_default_focus_is_the_two_strategies():
    assert DEFAULT_FOCUS == FOCUSABLE == {"snapback", "supertrend"}


def test_focus_allows_both_focused_strategies_to_originate():
    policy = parse_focus("snapback,supertrend")
    assert policy.may_originate("snapback")
    assert policy.may_originate("supertrend")


def test_focus_blocks_every_other_strategy_from_originating():
    policy = parse_focus("snapback,supertrend")
    for other in ("gamma_move", "adaptive_edge", "intraday", "navigator"):
        assert not policy.may_originate(other)
        assert "STRATEGY_NOT_FOCUSED" in policy.refusal_reason(other)


def test_focus_never_strands_an_existing_position():
    """A blocked engine must still reconcile and exit what it holds."""
    policy = parse_focus("snapback")
    assert not policy.may_originate("supertrend")
    assert policy.may_manage("supertrend")
    assert policy.may_manage("gamma_move")


def test_focus_can_narrow_to_one_strategy():
    policy = parse_focus("snapback")
    assert policy.may_originate("snapback")
    assert not policy.may_originate("supertrend")


def test_unset_focus_uses_the_declared_default():
    for raw in (None, "", "   ", ",,"):
        assert parse_focus(raw).originators == DEFAULT_FOCUS


def test_a_typo_fails_closed_instead_of_widening_risk():
    """``snapbak`` meant to restrict. Falling back to the default would widen."""
    with pytest.raises(FocusConfigurationError):
        parse_focus("snapbak")
    with pytest.raises(FocusConfigurationError):
        parse_focus("snapback,gamma_move")


# ── identity ──────────────────────────────────────────────────────────────


def _identity(**over):
    kwargs = dict(
        strategy_id="snapback",
        strategy_version="snapback_core_v1",
        mode="swing",
        mode_version="swing_v1",
        runtime_sha=RUNTIME,
        release_tag=TAG,
        config={"dte_min": 40, "dte_max": 60},
        rules={"entry": "bollinger_reversal"},
    )
    kwargs.update(over)
    return build_identity(**kwargs)


def test_identity_is_hashable_and_stable():
    assert _identity().identity_hash == _identity().identity_hash


def test_identity_ignores_mapping_insertion_order():
    a = _identity(config={"dte_min": 40, "dte_max": 60})
    b = _identity(config={"dte_max": 60, "dte_min": 40})
    assert a.identity_hash == b.identity_hash


def test_changing_a_rule_creates_a_new_identity():
    base = _identity()
    changed = _identity(rules={"entry": "bollinger_reversal_v2"})
    assert changed.identity_hash != base.identity_hash


def test_changing_a_config_value_creates_a_new_identity():
    assert _identity(config={"dte_min": 30, "dte_max": 45}).identity_hash != (
        _identity().identity_hash
    )


def test_lane_key_separates_the_ten_lanes():
    keys = {
        build_identity(
            strategy_id=strategy,
            strategy_version="v1",
            mode=mode,
            mode_version=f"{mode.value}_v1",
            runtime_sha=RUNTIME,
            release_tag=TAG,
            config={},
            rules={},
        ).lane_key
        for strategy in FOCUSABLE
        for mode in HorizonMode
    }
    assert len(keys) == 10


def test_two_modes_of_one_strategy_are_different_experiments():
    """Snapback scalping evidence can never satisfy the Snapback swing gate."""
    scalping = _identity(mode="scalping", mode_version="scalping_v1")
    swing = _identity(mode="swing", mode_version="swing_v1")
    assert scalping.lane_key != swing.lane_key
    assert scalping.identity_hash != swing.identity_hash


def test_two_strategies_at_one_mode_are_different_experiments():
    """SuperTrend swing evidence can never satisfy the Snapback swing gate."""
    snap = _identity(strategy_id="snapback")
    st = _identity(strategy_id="supertrend")
    assert snap.lane_key != st.lane_key
    assert snap.identity_hash != st.identity_hash


def test_legacy_spelling_does_not_split_a_lane():
    """``scalp`` and ``scalping`` are the same experiment, recorded as one."""
    legacy = _identity(mode="scalp", mode_version="scalping_v1")
    canonical = _identity(mode="scalping", mode_version="scalping_v1")
    assert legacy.mode is HorizonMode.SCALPING
    assert legacy.legacy_mode == "scalp"
    assert canonical.legacy_mode is None
    assert legacy.identity_hash == canonical.identity_hash


def test_identity_refuses_an_unfocused_strategy():
    with pytest.raises(IdentityError):
        _identity(strategy_id="gamma_move")


def test_identity_refuses_an_unknown_runtime():
    with pytest.raises(IdentityError):
        _identity(runtime_sha="UNKNOWN")


@pytest.mark.parametrize(
    "field", ["strategy_version", "mode_version", "release_tag"]
)
def test_identity_refuses_a_blank_required_field(field):
    with pytest.raises(IdentityError):
        _identity(**{field: "  "})


def test_identity_refuses_an_uncanonicalised_mode_string():
    with pytest.raises(IdentityError):
        StrategyModeIdentity(
            strategy_id="snapback",
            strategy_version="v1",
            mode="swing",  # a str, not a HorizonMode
            mode_version="swing_v1",
            runtime_sha=RUNTIME,
            release_tag=TAG,
            config_hash="a" * 16,
            rule_hash="b" * 16,
        )


def test_identity_row_carries_every_required_column():
    row = _identity().as_row()
    for column in (
        "strategy_id",
        "strategy_version",
        "mode",
        "mode_version",
        "runtime_sha",
        "release_tag",
        "config_hash",
        "rule_hash",
        "evidence_schema_version",
        "lane_key",
        "identity_hash",
    ):
        assert row.get(column), f"missing {column}"
