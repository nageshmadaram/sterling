"""E11/E12: Family Mode binds one exact account, and never wanders to another.

`all_accounts()[0]` means a disconnected family account silently hands the session to
whichever other account happens to be connected — the wrong money.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.snapback_family_account import (
    FamilyAccountBinding,
    FamilyAccountBindingError,
    configured_binding,
    resolve_family_account,
)


@pytest.fixture(autouse=True)
def _family_mode(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    monkeypatch.setenv("STERLING_FAMILY_USER_ID", "default")
    monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", "KITE-FAMILY")
    yield


def _account(account_id="KITE-FAMILY", user_id="default", connected=True):
    return SimpleNamespace(id=account_id, user_id=user_id, connected=connected,
                           label="Family", is_paper=False)


def test_the_binding_is_read_from_configuration():
    binding = configured_binding()

    assert binding == FamilyAccountBinding(user_id="default", account_id="KITE-FAMILY")


def test_a_missing_binding_fails_closed(monkeypatch):
    monkeypatch.delenv("STERLING_FAMILY_ACCOUNT_ID", raising=False)

    with pytest.raises(FamilyAccountBindingError):
        configured_binding()


def test_the_exact_account_is_resolved(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get",
        lambda user_id, account_id: _account(account_id, user_id),
    )

    account = resolve_family_account()

    assert account.id == "KITE-FAMILY"


def test_a_missing_account_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get", lambda user_id, account_id: None,
    )

    with pytest.raises(FamilyAccountBindingError):
        resolve_family_account()


def test_an_account_belonging_to_another_user_is_refused(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get",
        lambda user_id, account_id: _account(account_id, user_id="somebody_else"),
    )

    with pytest.raises(FamilyAccountBindingError):
        resolve_family_account()


def test_a_disconnected_account_keeps_its_identity(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get",
        lambda user_id, account_id: _account(connected=False),
    )

    account = resolve_family_account()

    # Identity is fine; the broker session is not. Those are different facts.
    assert account.id == "KITE-FAMILY"
    assert account.connected is False


def test_a_disconnected_family_account_never_falls_back_to_another(monkeypatch):
    others = [_account("KITE-OTHER", connected=True)]

    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get",
        lambda user_id, account_id: _account(connected=False),
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.all_accounts", lambda: others,
    )

    account = resolve_family_account()

    assert account.id == "KITE-FAMILY"
    assert account.id != "KITE-OTHER"


def test_no_family_runtime_path_uses_the_first_account():
    import inspect

    from app.services import snapback_health, snapback_runner

    for module in (snapback_runner, snapback_health):
        source = inspect.getsource(module)
        assert "all_accts[0]" not in source, module.__name__
        assert "all_accounts()[0]" not in source, module.__name__


def test_preflight_halts_without_a_binding(monkeypatch):
    monkeypatch.delenv("STERLING_FAMILY_ACCOUNT_ID", raising=False)

    from app.services.snapback_startup import run_startup_preflight

    result = run_startup_preflight(
        db_writable_fn=lambda: True, manifest_fn=lambda: (True, []),
        config_store_available_fn=lambda: True,
        config_fn=lambda: type("C", (), {"enabled": True})(),
        config_hash_fn=lambda cfg: "h", frozen_config_hash="h",
    )

    assert result.status == "HALTED"
    assert "family_account_binding_missing" in result.errors


def test_a_disconnected_token_is_not_a_preflight_failure(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get",
        lambda user_id, account_id: _account(connected=False),
    )

    from app.services.snapback_startup import run_startup_preflight

    result = run_startup_preflight(
        db_writable_fn=lambda: True, manifest_fn=lambda: (True, []),
        config_store_available_fn=lambda: True,
        config_fn=lambda: type("C", (), {"enabled": True})(),
        config_hash_fn=lambda cfg: "h", frozen_config_hash="h",
    )

    # Identity is configured and correct; the token being expired is operational.
    assert "family_account_binding_missing" not in result.errors


def test_health_separates_identity_from_connectedness():
    from app.services.snapback_health import build_prospective_health

    class W:
        def opportunity_status_counts(self):
            return {}

        def paper_position_status_counts(self):
            return {}

    from datetime import datetime, timezone

    now = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
    body = build_prospective_health(
        warehouse=W(), runtime_sha="sha", strategy_manifest="m", manifest_ok=True,
        mode="PAPER", broker_connected=False, market_data_fresh=False,
        calendar_ok=True, database_ok=True, runner_alive=True, last_runner_tick=now,
        family_account={"family_account_configured": True,
                        "family_account_identity_ok": True,
                        "family_account_id": "KITE-…MILY"},
        market_open=False, now=now,
    )

    assert body["family_account_configured"] is True
    assert body["family_account_identity_ok"] is True
    assert body["broker_connected"] is False
