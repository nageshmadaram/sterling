"""Admission by venue: a position is opened only when its whole lifecycle can
address the right one.

SENSEX was refused here while the post-entry path rebuilt NSE/NFO quote keys
from canonical names. Now that every key comes from the identity stored at
signal time, it is admitted like any other underlying. The containment is gone
and this file is the record of that, not a second copy of it."""

from app.services.snapback_capacity import ObservedHedgeMargin, evaluate_capacity


def test_sensex_is_admitted_now_that_the_lifecycle_is_identity_aware(monkeypatch, tmp_path):
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

    assert decision.allowed is True
    assert decision.status == "CAPACITY_OK"
    # The venue itself is proved end to end in test_sensex_venue_lifecycle.py;
    # what matters here is that nothing refuses it on venue grounds any more.
    assert decision.reasons == []


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
