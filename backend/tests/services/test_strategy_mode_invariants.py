"""One rule, checked across every Kite strategy at once.

Three switches govern all of them, and each has exactly one home:

* **PAPER / LIVE** — `account.is_paper`. `KiteClient` enforces it at the order
  boundary, so no strategy needs its own copy and a copy can only disagree.
* **MANUAL / AUTO** — the engine's `auto_execute`. It gates a strategy OPENING a
  position by itself. It must NOT gate a human pressing the button, and it must
  NOT gate maintaining or exiting a position already held — you always have to be
  able to get out of what you are in.
* **ON / OFF** — each engine's own `enabled`. There is no global equivalent.

These live in one file because the failure they guard against is drift: a new
engine that quietly grows its own switch, or an old one whose gate is removed in
a refactor. A per-engine test would not notice either.
"""
from __future__ import annotations

import inspect

import pytest

# (label, module path) for every service that can open a position on its own.
AUTO_OPENING_PATHS = [
    ("opening_volume_leaders", "app.services.opening_volume_execution"),
    ("gamma_move", "app.services.gamma_move_runner"),
    ("supertrend", "app.services.kite_engine.service"),
    ("navigator", "app.services.navigator.runtime"),
]


@pytest.mark.parametrize("label,module_path", AUTO_OPENING_PATHS)
def test_every_auto_opening_path_consults_auto_execute(label, module_path):
    """No strategy may open a position by itself while the engine is on MANUAL."""
    module = __import__(module_path, fromlist=["x"])
    assert "auto_execute" in inspect.getsource(module), (
        f"{label} can open a position but never reads auto_execute — "
        "it would trade in MANUAL")


CONFIGS = [
    ("opening_volume_leaders", "app.services.opening_volume_execution", "OpeningExecutionConfig"),
    ("gamma_move", "app.engines.gamma_move.config", "GammaMoveConfig"),
]


@pytest.mark.parametrize("label,module_path,cls_name", CONFIGS)
def test_no_strategy_config_carries_its_own_paper_live_switch(label, module_path, cls_name):
    """Paper/live is the account's. A second copy can disagree with the client
    that actually places the order — and has: a config reading "paper" against a
    live account was observed on 2026-08-26."""
    cls = getattr(__import__(module_path, fromlist=["x"]), cls_name)
    names = {f.name for f in __import__("dataclasses").fields(cls)}
    assert "execution_mode" not in names, f"{label} carries its own paper/live switch"
    assert "paper_only" not in names
    assert "is_paper" not in names


EXIT_PATHS = [
    ("gamma_move exits", "app.services.gamma_move_runner", "on_ticks"),
    ("supertrend monitor", "app.services.kite_engine.monitor", None),
]


@pytest.mark.parametrize("label,module_path,func_name", EXIT_PATHS)
def test_exits_are_never_gated_on_auto_execute(label, module_path, func_name):
    """Turning AUTO off must not strand an open position.

    Maintaining or closing what you already hold is not the same decision as
    opening something new, and the SuperTrend engine has already had to fix
    exactly this — trailing froze on live positions when AUTO went off.
    """
    module = __import__(module_path, fromlist=["x"])
    src = inspect.getsource(getattr(module, func_name) if func_name else module)
    offending = [ln for ln in src.splitlines()
                 if "auto_execute" in ln and "not " in ln and "#" not in ln.split("auto_execute")[0]]
    assert not offending, f"{label} gates an exit on auto_execute: {offending}"


def test_option_engine_power_switch_defaults():
    from app.engines.gamma_move import GammaMoveConfig
    from app.services.opening_volume_execution import OpeningExecutionConfig
    assert GammaMoveConfig().enabled is True
    assert OpeningExecutionConfig().enabled is True
