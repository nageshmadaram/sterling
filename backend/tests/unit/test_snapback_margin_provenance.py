"""Capacity may use a hedge margin only when the broker actually supplied it."""

from app.services.snapback_capacity import ObservedHedgeMargin, evaluate_capacity


def _decision(margin):
    return evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=margin,
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="NIFTY",
        open_underlyings=set(),
    )


def test_plain_numeric_margin_is_not_authoritative_in_production(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from tests.conftest import normal_safe_mode_file

    normal_safe_mode_file(monkeypatch, str(tmp_path / "safe.json"))
    decision = _decision(120_000.0)

    assert decision.allowed is False
    assert decision.status == "INCONCLUSIVE_CAPACITY"
    assert "hedge_margin_not_broker_observed" in decision.reasons


def test_broker_observed_margin_is_admitted_in_production(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from tests.conftest import normal_safe_mode_file

    normal_safe_mode_file(monkeypatch, str(tmp_path / "safe.json"))
    decision = _decision(ObservedHedgeMargin(120_000.0))

    assert decision.allowed is True
    assert decision.status == "CAPACITY_OK"
