"""Snapback validation records are tied to the implementation that produced them."""
from __future__ import annotations

from app.services import snapback_validation as val


def _store(monkeypatch, raw):
    from app.services import db
    monkeypatch.setattr(db, "get_config", lambda key: raw)
    writes = []
    monkeypatch.setattr(db, "set_config", lambda key, value: writes.append((key, value)))
    return writes


def promoted(**kw) -> val.Validation:
    data = val.current_manifest()
    data.update(kw)
    return val.Validation(promoted=True, measured_at="2026-09-13", checks={"ok": True}, **data)


def test_record_writes_the_current_manifest(monkeypatch):
    writes = _store(monkeypatch, "")
    assert val.record(val.Validation(promoted=True, measured_at="2026-09-13")) is True
    assert writes
    assert val.COST_MODEL_VERSION in writes[0][1]


def test_promoted_record_without_a_manifest_is_stale(monkeypatch):
    _store(monkeypatch, '{"promoted":true,"measured_at":"2026-09-13"}')
    assert val.is_promoted() is False
    why = val.auto_execution_blocker()
    assert why and "stale" in why and "engine_version" in why


def test_promoted_record_with_current_manifest_is_accepted(monkeypatch):
    _store(monkeypatch, promoted().as_dict())
    assert val.is_promoted() is True
    assert val.auto_execution_blocker() is None


def test_config_change_invalidates_validation(monkeypatch):
    from dataclasses import replace
    from app.engines.snapback import SnapbackConfig

    original = SnapbackConfig()
    changed = replace(original, hold_days=original.hold_days + 1)
    rec = promoted(config_hash=val.current_manifest(original)["config_hash"]).as_dict()
    assert val.is_compatible(rec, original)
    assert not val.is_compatible(rec, changed)
