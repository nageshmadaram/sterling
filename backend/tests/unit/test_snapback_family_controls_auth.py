"""Family controls are operator actions, not anonymous ones."""

from __future__ import annotations

import inspect

import pytest


def test_family_endpoints_require_an_authenticated_operator():
    from app.api.v1.endpoints import snapback_ops

    for name in ("family_operations", "family_stop_new_trades", "family_resume_new_trades"):
        fn = getattr(snapback_ops, name)
        params = inspect.signature(fn).parameters
        assert "user" in params, f"{name} does not require an operator"


def test_mutations_record_the_actor():
    from app.api.v1.endpoints import snapback_ops

    for name in ("family_stop_new_trades", "family_resume_new_trades"):
        source = inspect.getsource(getattr(snapback_ops, name))
        assert "actor" in source


def test_halt_state_records_who_changed_it(tmp_path, monkeypatch):
    import json

    import app.services.snapback_family_ops as fam

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    fam.set_new_trades_halted(True, reason="family stop switch (actor=operator)")

    payload = json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))

    assert "operator" in payload["reason"]
    assert payload["changed_at"]
