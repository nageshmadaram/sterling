"""E03: while a prospective experiment is running, the hypothesis cannot change.

A config edit mid-experiment silently splits the sample into two strategies, and the
evidence then answers a question nobody asked. Operational controls are separate: the
family STOP switch already halts new exposure without touching the hypothesis.
"""

from __future__ import annotations

import pytest

from app.services.snapback import (
    FrozenExperimentConfigError,
    get_config,
    prospective_experiment_active,
    set_config,
)


@pytest.fixture
def active_experiment(monkeypatch, tmp_path):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    monkeypatch.setenv(
        "STERLING_OBSERVATIONS_DB_PATH", str(tmp_path / "prospective_runtime_1_1.db"),
    )
    yield


@pytest.fixture
def research_environment(monkeypatch):
    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)
    monkeypatch.setenv("STERLING_PROSPECTIVE_EXPERIMENT", "false")
    yield


def test_the_experiment_is_detected_as_active(active_experiment):
    assert prospective_experiment_active() is True


def test_a_research_environment_is_not_frozen(research_environment):
    assert prospective_experiment_active() is False


@pytest.mark.parametrize("field,value", [
    ("lookback_days", 30),
    ("target_delta", 0.5),
    ("enabled", False),
    ("auto_execute", True),
    ("min_stretch_atr", 2.0),
    ("hold_days", 20),
])
def test_every_strategy_field_is_frozen(active_experiment, field, value):
    with pytest.raises(FrozenExperimentConfigError):
        set_config({field: value}, uid="default")


def test_the_service_boundary_is_what_refuses(active_experiment):
    # Not only the endpoint: a script, a test or an internal caller is refused too.
    with pytest.raises(FrozenExperimentConfigError) as excinfo:
        set_config({"lookback_days": 25})

    assert "frozen" in str(excinfo.value).lower()


def test_a_rejected_edit_leaves_the_config_hash_unchanged(active_experiment):
    from app.engines.snapback.manifest import compute_config_hash

    before = compute_config_hash(get_config("default"))

    with pytest.raises(FrozenExperimentConfigError):
        set_config({"target_delta": 0.55}, uid="default")

    assert compute_config_hash(get_config("default")) == before


def test_research_environments_can_still_edit(research_environment, monkeypatch):
    stored = {}

    monkeypatch.setattr("app.services.db.set_config",
                        lambda key, value: stored.update({key: value}))
    monkeypatch.setattr("app.services.db.is_available", lambda: True)

    set_config({"lookback_days": 25}, uid="research")

    assert stored


def test_family_stop_does_not_touch_the_config(active_experiment, tmp_path, monkeypatch):
    import app.services.snapback_family_ops as fam
    from app.engines.snapback.manifest import compute_config_hash

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))
    before = compute_config_hash(get_config("default"))

    fam.set_new_trades_halted(True, reason="family stop")
    assert fam.new_trades_halted() is True

    fam.set_new_trades_halted(False, reason="family resume")
    assert fam.new_trades_halted() is False

    # Operational state changed; the hypothesis did not.
    assert compute_config_hash(get_config("default")) == before


def test_the_endpoint_maps_the_refusal_to_409(active_experiment):
    import inspect

    from app.api.v1.endpoints import config as config_endpoint

    source = inspect.getsource(config_endpoint)

    assert "FrozenExperimentConfigError" in source
    assert "SNAPBACK_CONFIG_FROZEN" in source
    assert "409" in source


def test_an_open_position_carries_its_own_policy_snapshot(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    wh.record_opportunity(
        opportunity_id="OPP-1", symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, trend="BEARISH",
        policy_snapshot_hash="policy-abc", config_hash="cfg-abc",
    )

    row = wh.get_opportunity_by_id("OPP-1")

    assert row["policy_snapshot_hash"] == "policy-abc"
    assert row["config_hash"] == "cfg-abc"
