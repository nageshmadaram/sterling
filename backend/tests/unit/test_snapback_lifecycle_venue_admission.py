"""Do not open a position whose later evidence path addresses the wrong venue."""

from app.services.snapback_capacity import ObservedHedgeMargin, evaluate_capacity


def test_sensex_is_recordable_but_not_admitted_until_bfo_lifecycle_is_identity_aware(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(tmp_path / "safe.json"))

    decision = evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=ObservedHedgeMargin(100_000.0),
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="SENSEX",
        open_underlyings=set(),
    )

    assert decision.allowed is False
    assert decision.status == "INCONCLUSIVE_LIFECYCLE_VENUE"
    assert decision.reasons == ["post_entry_identity_routing_unverified:SENSEX"]


def test_nifty_remains_admissible_when_other_capacity_inputs_are_authoritative(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(tmp_path / "safe.json"))

    decision = evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=ObservedHedgeMargin(100_000.0),
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="NIFTY",
        open_underlyings=set(),
    )

    assert decision.allowed is True
    assert decision.status == "CAPACITY_OK"
