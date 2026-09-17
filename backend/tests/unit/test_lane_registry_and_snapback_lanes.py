"""The ten lanes, the origination gate, and Snapback's mode adapters."""
from __future__ import annotations

import pytest

from app.core.focus import parse_focus
from app.core.horizon import HorizonMode, LaneState
from app.core.lane_registry import (
    LANES,
    LIVE_EXECUTION_ENABLED,
    UnknownLane,
    all_lanes,
    dashboard_rows,
    get_lane,
    lanes_for,
    may_originate,
)
from app.engines.snapback import lanes as snapback_lanes
from app.engines.snapback.config import TRADING_MODES
from app.engines.snapback.manifest import compute_rule_hash

BOTH = parse_focus("snapback,supertrend")
RUNTIME = "b" * 40
TAG = "snapback-prospective-runtime-1.6"


# ── registry shape ────────────────────────────────────────────────────────


def test_there_are_exactly_ten_lanes():
    assert len(LANES) == 10
    assert len(lanes_for("snapback")) == 5
    assert len(lanes_for("supertrend")) == 5


def test_every_strategy_has_every_canonical_mode():
    for strategy in ("snapback", "supertrend"):
        modes = {lane.mode for lane in lanes_for(strategy)}
        assert modes == set(HorizonMode)


def test_lane_keys_are_unique():
    keys = [lane.lane_key for lane in all_lanes()]
    assert len(keys) == len(set(keys))


def test_unknown_lane_raises():
    with pytest.raises(UnknownLane):
        get_lane("gamma_move", "swing")


# ── the live floor ────────────────────────────────────────────────────────


def test_real_money_execution_is_disabled_in_code():
    assert LIVE_EXECUTION_ENABLED is False


def test_no_lane_starts_in_a_live_state():
    for lane in all_lanes():
        assert lane.state not in (LaneState.LIVE_MINIMUM, LaneState.LIVE_SCALED)


def test_a_live_lane_still_cannot_originate_while_the_global_switch_is_off(
    monkeypatch,
):
    """Promoting a lane must not be enough on its own."""
    lane = get_lane("snapback", "swing")
    monkeypatch.setitem(
        LANES,
        lane.lane_key,
        type(lane)(
            strategy_id=lane.strategy_id,
            mode=lane.mode,
            state=LaneState.LIVE_MINIMUM,
            rules_defined=True,
            note="promoted for this test",
        ),
    )
    decision = may_originate("snapback", "swing", policy=BOTH, live_enabled=False)
    assert decision.allowed is False
    assert decision.reason == "LIVE_EXECUTION_DISABLED"
    assert may_originate(
        "snapback", "swing", policy=BOTH, live_enabled=True
    ).allowed is True


# ── origination gate ──────────────────────────────────────────────────────


def test_a_paper_lane_with_defined_rules_may_originate():
    assert may_originate("snapback", "swing", policy=BOTH).allowed is True
    assert may_originate("snapback", "scalping", policy=BOTH).allowed is True


def test_a_research_lane_may_not_originate():
    decision = may_originate("supertrend", "swing", policy=BOTH)
    assert decision.allowed is False
    assert decision.reason == "LANE_STATE_BLOCKS_ORIGINATION"


def test_a_lane_without_rules_cannot_originate_even_while_research():
    """State and rule-definedness are separate refusals.

    Snapback Overnight reads RESEARCH, but its gap, DTE, theta, stop and hedge
    policies are undefined, so there is nothing to hash and nothing to attribute
    evidence to.
    """
    for mode in ("ultra_scalping", "overnight"):
        decision = may_originate("snapback", mode, policy=BOTH)
        assert decision.allowed is False
        assert decision.reason == "LANE_RULES_UNDEFINED"


def test_snapback_intraday_is_blocked_because_it_has_no_rules_of_its_own():
    """The runtime shares the scalp_* path and knobs between the two labels.

    Gating them as two lanes would demand two 300-trade samples from one rule
    and let one edge be counted twice.
    """
    lane = get_lane("snapback", "intraday")
    assert lane.rules_defined is False
    decision = may_originate("snapback", "intraday", policy=BOTH)
    assert decision.reason == "LANE_RULES_UNDEFINED"


def test_focus_refusal_outranks_a_lane_refusal():
    """The operator needs the reason they can act on."""
    only_supertrend = parse_focus("supertrend")
    decision = may_originate("snapback", "overnight", policy=only_supertrend)
    assert decision.reason == "STRATEGY_NOT_FOCUSED"


def test_dashboard_lists_every_lane_with_a_reason():
    rows = dashboard_rows()
    assert len(rows) == 10
    for row in rows:
        assert row["note"]
        if not row["may_originate"]:
            assert row["reason"]


# ── snapback adapters ─────────────────────────────────────────────────────


def test_every_legacy_engine_mode_maps_to_a_canonical_lane():
    assert set(TRADING_MODES) == set(snapback_lanes.LEGACY_MODE_MAP)
    for legacy, canonical in snapback_lanes.LEGACY_MODE_MAP.items():
        assert snapback_lanes.canonical_mode_of_engine(legacy) is canonical


def test_scalp_is_the_only_rename():
    assert snapback_lanes.engine_mode_of("scalping") == "scalp"
    assert snapback_lanes.engine_mode_of("swing") == "swing"
    assert snapback_lanes.engine_mode_of("intraday") == "intraday"


def test_new_modes_have_no_engine_counterpart():
    """Returning a plausible string would send them down an existing path."""
    assert snapback_lanes.engine_mode_of("ultra_scalping") is None
    assert snapback_lanes.engine_mode_of("overnight") is None


def test_the_legacy_spelling_is_recorded_on_the_identity():
    identity = snapback_lanes.identity_for(
        "scalp", runtime_sha=RUNTIME, release_tag=TAG
    )
    assert identity.mode is HorizonMode.SCALPING
    assert identity.legacy_mode == "scalp"
    assert identity.lane_key == "snapback:scalping"


def test_the_manifest_rule_hash_cannot_tell_two_modes_apart():
    """The defect the lane hash exists to correct.

    compute_rule_hash covers only the daily swing rules, so it is blind to
    trading_mode and to every scalp_* knob.
    """
    from app.engines.snapback.config import SnapbackConfig

    swing = SnapbackConfig(trading_mode="swing")
    scalp = SnapbackConfig(trading_mode="scalp", scalp_target_points=9.0)
    assert compute_rule_hash(swing) == compute_rule_hash(scalp)


def test_the_lane_rule_hash_separates_all_five_modes():
    hashes = {
        mode: snapback_lanes.lane_rule_hash(mode) for mode in HorizonMode
    }
    assert len(set(hashes.values())) == 5


def test_a_changed_scalp_knob_changes_only_the_scalping_lane_hash():
    from app.engines.snapback.config import SnapbackConfig

    base = SnapbackConfig()
    tweaked = SnapbackConfig(scalp_target_points=9.0)
    assert snapback_lanes.lane_rule_hash("scalping", tweaked) != (
        snapback_lanes.lane_rule_hash("scalping", base)
    )
    assert snapback_lanes.lane_rule_hash("swing", tweaked) == (
        snapback_lanes.lane_rule_hash("swing", base)
    )


def test_identities_for_the_five_modes_are_ten_distinct_things_with_supertrend():
    snap = {
        snapback_lanes.identity_for(
            mode, runtime_sha=RUNTIME, release_tag=TAG
        ).identity_hash
        for mode in HorizonMode
    }
    assert len(snap) == 5


def test_identity_is_stable_across_calls():
    a = snapback_lanes.identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    b = snapback_lanes.identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    assert a.identity_hash == b.identity_hash


def test_a_different_release_is_a_different_identity():
    a = snapback_lanes.identity_for("swing", runtime_sha=RUNTIME, release_tag=TAG)
    b = snapback_lanes.identity_for(
        "swing", runtime_sha=RUNTIME, release_tag="runtime-1.7"
    )
    assert a.identity_hash != b.identity_hash
