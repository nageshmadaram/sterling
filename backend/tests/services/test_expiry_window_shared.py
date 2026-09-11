"""The expiry window, checked across every engine at once.

Minimum days to expiry, maximum days to expiry and expiry day are now settings on
every strategy, and they mean the same thing on each. That is a claim about all
of them together, so it is tested together — a per-engine test would not notice a
new engine quietly growing its own vocabulary, which is how three pages ended up
saying three different things in the first place.
"""
from __future__ import annotations

import pytest

from app.services.kite_engine.strikes import expiry_window_of, in_expiry_window

CONFIGS = [
    ("supertrend", "app.engines.sterling_kite_engine.schemas", "EngineConfigModel"),
    ("navigator", "app.engines.navigator.schemas", "NavigatorConfigModel"),
    ("gamma_move", "app.engines.gamma_move.config", "GammaMoveConfig"),
]


@pytest.mark.parametrize("label,module,cls_name", CONFIGS)
def test_every_engine_carries_the_three_settings(label, module, cls_name):
    cls = getattr(__import__(module, fromlist=["x"]), cls_name)
    fields = set(getattr(cls, "model_fields", None) or
                 {f.name for f in __import__("dataclasses").fields(cls)})
    for name in ("expiry_dte_min", "expiry_dte_max", "avoid_expiry_day"):
        assert name in fields, f"{label} has no {name}"


def test_adaptive_edge_carries_them_too():
    from app.api.v1.endpoints.adaptive_edge import AdaptiveEdgeSettings
    for name in ("expiry_dte_min", "expiry_dte_max", "avoid_expiry_day"):
        assert name in AdaptiveEdgeSettings.model_fields


class TestTheSharedPredicate:
    """One predicate, so the words cannot come to mean different things."""

    def test_expiry_day_is_excluded_only_when_asked(self):
        assert in_expiry_window({"dte": 0}) is True
        assert in_expiry_window({"dte": 0}, avoid_expiry_day=True) is False

    def test_the_window_is_inclusive_at_both_ends(self):
        assert in_expiry_window({"dte": 1}, min_dte=1, max_dte=14) is True
        assert in_expiry_window({"dte": 14}, min_dte=1, max_dte=14) is True
        assert in_expiry_window({"dte": 0}, min_dte=1, max_dte=14) is False
        assert in_expiry_window({"dte": 15}, min_dte=1, max_dte=14) is False

    def test_no_maximum_means_no_ceiling(self):
        assert in_expiry_window({"dte": 3650}, max_dte=None) is True

    def test_defaults_admit_everything(self):
        """Every existing caller passes nothing and must resolve what it did."""
        for dte in (0, 1, 7, 400):
            assert in_expiry_window({"dte": dte}) is True


class TestNavigatorFollowsTheEngineUntilItDoesNot:
    """Navigator's fields are Optional and mean 'follow SuperTrend' when unset —
    the same rule its strike ladder and expiry cycles already use."""

    def cfgs(self, **nav):
        from app.engines.navigator.schemas import NavigatorConfigModel
        from app.engines.sterling_kite_engine.schemas import EngineConfigModel
        return NavigatorConfigModel(**nav), EngineConfigModel(
            expiry_dte_min=2, expiry_dte_max=14, avoid_expiry_day=True)

    def test_unset_follows_the_engine(self):
        nav, engine = self.cfgs()
        assert expiry_window_of(nav, engine) == {
            "min_dte": 2, "max_dte": 14, "avoid_expiry_day": True}

    def test_a_set_field_overrides_only_itself(self):
        nav, engine = self.cfgs(expiry_dte_max=7)
        window = expiry_window_of(nav, engine)
        assert window["max_dte"] == 7          # Navigator's own
        assert window["min_dte"] == 2          # still the engine's

    def test_no_fallback_falls_back_to_permissive(self):
        nav, _ = self.cfgs()
        assert expiry_window_of(nav) == {
            "min_dte": 0, "max_dte": None, "avoid_expiry_day": False}
