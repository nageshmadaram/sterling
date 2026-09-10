"""Config validation, with the emphasis on the thresholds that must not be zero."""
from __future__ import annotations

import pytest

from app.engines.gamma_move import CALIBRATED_FIELDS, GammaMoveConfig


def test_defaults_validate():
    cfg = GammaMoveConfig().validate()
    assert cfg.stop_mode == "both"


def test_the_engine_ships_on():
    """`enabled` is a power switch, not a safety device.

    Paper/live, manual/auto, the kill switch and the risk caps are what stand
    between this engine and real money, and every one of them applies whatever
    this flag says. Shipping off would only hide the strategy from its operator.
    """
    assert GammaMoveConfig().enabled is True


def test_there_is_no_execution_mode():
    """Paper/live for Kite is `account.is_paper`, and a copy here could disagree
    with the client that actually places the order."""
    assert "execution_mode" not in GammaMoveConfig.field_names()
    assert "protection_mode" not in GammaMoveConfig.field_names()


def test_universe_uses_the_shared_registry_vocabulary():
    names = GammaMoveConfig.field_names()
    assert {"scan_stocks", "scan_all_stocks", "stock_contracts", "scan_indices"} <= names
    # The invented parallel vocabulary is gone.
    assert not {"max_universe", "explicit_symbols", "include_indices"} & names


def test_off_registry_stock_is_refused_not_dropped():
    with pytest.raises(ValueError, match="curated high-liquidity registry"):
        GammaMoveConfig(scan_stocks=("SOMEPENNYCO",)).validate()


def test_scanning_nothing_is_refused():
    with pytest.raises(ValueError, match="nothing to scan"):
        GammaMoveConfig(stock_contracts=False, scan_indices=()).validate()


def test_defaults_are_the_calibrated_values():
    """The measured numbers, locked. If one changes, the docstring and the
    validation report have to change with it."""
    cfg = GammaMoveConfig()
    assert cfg.level_proximity_pct == 1.0
    assert cfg.min_oi_drop_pct == 3.0
    assert cfg.volume_spike_mult == 2.5
    assert cfg.min_price_gain_pct == 2.0
    assert cfg.regime_period == 10
    # 2.0, not the conventional 3.0: at 3.0 the gate measured *inverted*.
    assert cfg.regime_multiplier == 2.0
    assert cfg.min_option_premium == 10.0
    assert cfg.stop_percent == 30.0


def test_every_calibrated_field_is_a_real_field():
    names = GammaMoveConfig.field_names()
    assert CALIBRATED_FIELDS <= names


@pytest.mark.parametrize("field", ["level_proximity_pct", "min_oi_drop_pct",
                                   "min_price_gain_pct"])
def test_zero_threshold_is_refused(field):
    """Zero does not disable these — it makes the condition trivially true,
    which silently deletes part of the entry rule while looking like a setting."""
    with pytest.raises(ValueError, match=field):
        GammaMoveConfig(**{field: 0}).validate()


def test_volume_multiplier_must_exceed_one():
    with pytest.raises(ValueError, match="volume_spike_mult"):
        GammaMoveConfig(volume_spike_mult=1.0).validate()
    with pytest.raises(ValueError, match="volume_spike_mult"):
        GammaMoveConfig(volume_spike_mult=0.5).validate()


def test_contract_settings_use_the_shared_vocabulary():
    """Same field names and meanings as every other engine, so a reader does not
    have to learn a private vocabulary per strategy."""
    from app.engines.option_contracts import EXPIRY_SELECTIONS
    from app.engines.nifty_orb_options import EXPIRY_SELECTIONS as ORB_SELECTIONS
    assert EXPIRY_SELECTIONS is ORB_SELECTIONS      # one object, not two equal ones
    names = GammaMoveConfig.field_names()
    assert {"expiry_selection", "expiry_dte_min", "expiry_dte_max",
            "avoid_expiry_day"} <= names
    assert not {"min_days_to_expiry", "max_days_to_expiry"} & names


def test_expiry_window_must_be_ordered():
    with pytest.raises(ValueError, match="expiry_dte_max"):
        GammaMoveConfig(expiry_dte_min=20, expiry_dte_max=14).validate()


def test_avoid_expiry_day_with_a_zero_window_leaves_nothing():
    with pytest.raises(ValueError, match="no eligible expiry"):
        GammaMoveConfig(expiry_dte_min=0, expiry_dte_max=0).validate()


def test_expiry_day_is_actually_excluded():
    """The flag has to reach the selection, not just validate."""
    from datetime import date
    from app.engines.gamma_move import expiry_in_window
    today = date(2026, 9, 29)
    cfg = GammaMoveConfig(avoid_expiry_day=True).validate()
    assert expiry_in_window("2026-09-29", today, cfg) is False      # expiry day
    assert expiry_in_window("2026-10-01", today, cfg) is True
    allow = GammaMoveConfig(avoid_expiry_day=False).validate()
    assert expiry_in_window("2026-09-29", today, allow) is True


def test_confirm_bars_bounded():
    GammaMoveConfig(confirm_bars=3).validate()
    with pytest.raises(ValueError, match="confirm_bars"):
        GammaMoveConfig(confirm_bars=4).validate()
    with pytest.raises(ValueError, match="confirm_bars"):
        GammaMoveConfig(confirm_bars=0).validate()


def test_hundred_percent_stop_is_the_premium():
    with pytest.raises(ValueError, match="100% stop is the premium"):
        GammaMoveConfig(stop_percent=100).validate()


def test_percent_target_needs_a_target():
    with pytest.raises(ValueError, match="target_pct"):
        GammaMoveConfig(exit_policy="PERCENT_TARGET", target_pct=0).validate()


class TestAlwaysOnInvariants:
    """Safety rules that do not depend on whose money it is.

    These used to sit behind `execution_mode == "live"`. A rule that only holds
    when the money is real is a rule the paper results were never measured
    under — a paper run that trades unprotected is not a rehearsal.
    """

    def test_a_stop_is_always_required(self):
        with pytest.raises(ValueError, match="a stop is required"):
            GammaMoveConfig(stop_percent=0, stop_points=0).validate()

    def test_a_percent_stop_satisfies_it(self):
        GammaMoveConfig(stop_basis="PERCENT", stop_percent=30).validate()

    def test_a_points_stop_satisfies_it_but_warns(self):
        cfg = GammaMoveConfig(stop_basis="POINTS", stop_points=8).validate()
        assert any("POINTS" in w for w in cfg.warnings())


class TestWarnings:
    """Configured risks are reported, not refused — the operator decides."""

    def test_defaults_are_quiet(self):
        assert GammaMoveConfig().validate().warnings() == []

    def test_monitor_only_warns_about_the_broker(self):
        cfg = GammaMoveConfig(stop_mode="monitor").validate()
        assert any("unprotected" in w for w in cfg.warnings())

    def test_unsourced_exit_warns(self):
        cfg = GammaMoveConfig(exit_policy="PERCENT_TARGET", target_pct=50).validate()
        assert any("not supported by the source" in w for w in cfg.warnings())

    def test_disabling_the_trend_gate_warns(self):
        cfg = GammaMoveConfig(regime_enabled=False).validate()
        assert any("corrective market" in w for w in cfg.warnings())


def test_as_dict_round_trips():
    cfg = GammaMoveConfig(min_oi_drop_pct=4.5, scan_stocks=("RELIANCE",))
    again = GammaMoveConfig(**{**cfg.as_dict(),
                               "scan_stocks": tuple(cfg.as_dict()["scan_stocks"])})
    assert again.min_oi_drop_pct == 4.5
    assert again.scan_stocks == ("RELIANCE",)


def test_source_flags_are_real_fields():
    """getattr(..., True) cannot be persisted. These must sit on the dataclass."""
    cfg = GammaMoveConfig(
        require_chain_max_oi=False,
        require_spot_through_strike=False,
    ).validate()
    assert cfg.require_chain_max_oi is False
    assert cfg.require_spot_through_strike is False
    dumped = cfg.as_dict()
    assert dumped["require_chain_max_oi"] is False
    assert dumped["require_spot_through_strike"] is False
    assert {"require_chain_max_oi", "require_spot_through_strike"} <= GammaMoveConfig.field_names()


def test_source_flags_default_on():
    cfg = GammaMoveConfig()
    assert cfg.require_chain_max_oi is True
    assert cfg.require_spot_through_strike is True
