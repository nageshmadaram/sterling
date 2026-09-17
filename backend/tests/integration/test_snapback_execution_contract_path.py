"""The production path, not a helper in isolation.

Runtime 1.6's evidence modules are worth nothing if the code that actually
trades never calls them. These tests drive
``SnapbackProspectiveCollector.execute_pending_entry`` — the real T+1 entry
method — and assert that the execution-contract evidence appears as a
side effect of a genuine entry attempt.

They also pin the distinction the release turns on. The frozen closed-form rule
produces a theoretical target at signal time; the collector buys the eligible
candidate from the real chain closest to target delta. Those are two decisions,
and the gap between them is the quantity being measured.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.models import SnapbackSignal
from app.services.snapback_execution_contract import (
    NO_ELIGIBLE_CANDIDATE,
    ExecutionContractError,
)
from app.services.snapback_prospective_collector import (
    OptionCandidateInfo,
    RawQuoteEvent,
    SnapbackProspectiveCollector,
)
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

T1 = datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc)
T1_MS = int(T1.timestamp() * 1000)


@pytest.fixture
def warehouse(tmp_path):
    return SnapbackObservationWarehouse(db_path=str(tmp_path / "evidence.db"))


@pytest.fixture
def collector(warehouse):
    return SnapbackProspectiveCollector(warehouse=warehouse)


@pytest.fixture
def cfg():
    return SnapbackConfig()


def _signal():
    return SnapbackSignal(
        symbol="NIFTY", side="fade_up", direction="MEAN_REVERT", option_type="PE",
        timestamp_ms=int(datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc).timestamp() * 1000),
        entry=24500.0, mean_target=24800.0, stretch=2.4, atr=180.0,
        realized_vol=0.16, assumed_iv=0.18, level=24400.0, strength="STRONG",
    )


def _identity():
    """The provider identity a real scan would have captured for NIFTY."""
    from app.services.snapback_instrument_identity import identity_from_instrument

    return identity_from_instrument(
        {"tradingsymbol": "NIFTY 50", "instrument_token": 256265,
         "exchange": "NSE", "name": "NIFTY"},
        canonical_symbol="NIFTY",
    )


@pytest.fixture(autouse=True)
def _observed_opening_window(monkeypatch):
    """These tests exercise the evidence hook, not observation continuity.

    Continuity has its own proof; presenting the opening window as observed here
    keeps the invariant under test the one that decides.
    """
    from app.services.snapback_entry_observation import ContinuityVerdict

    monkeypatch.setattr(
        "app.services.snapback.session_continuity",
        lambda *a, **k: ContinuityVerdict(continuous=True, reasons=[]),
        raising=False,
    )


def _candidate(strike, *, delta=0.70, symbol=None, dte=45, lot=65):
    return OptionCandidateInfo(
        symbol=symbol or f"NIFTY26OCT{int(strike)}PE",
        expiry="2026-10-29", strike=float(strike), option_type="PE",
        dte=dte, is_monthly=True, theoretical_delta=delta,
        instrument_token=str(int(strike)), lot_size=lot,
    )


def _quote(symbol, *, bid=450.0, ask=452.0, oi=60_000):
    return RawQuoteEvent(
        contract_id=symbol, exchange_timestamp_ms=T1_MS - 200,
        received_at_ms=T1_MS - 100, best_bid=bid, best_ask=ask,
        bid_quantity=500, ask_quantity=500, last_price=(bid + ask) / 2,
        open_interest=oi,
    )


def _futures():
    return RawQuoteEvent(
        contract_id="NIFTY-I", exchange_timestamp_ms=T1_MS - 200,
        received_at_ms=T1_MS - 100, best_bid=24510.0, best_ask=24512.0,
        bid_quantity=100_000, ask_quantity=100_000, last_price=24511.0,
        open_interest=500_000,
    )


def _enter(collector, cfg, candidates, quotes):
    recorded = collector.record_signal_at_close(signal=_signal(), cfg=cfg, identity=_identity())
    opportunity_id = recorded["opportunity_id"]
    result = collector.execute_pending_entry(
        opportunity_id=opportunity_id, cfg=cfg, t1_spot_price=24510.0,
        futures_quote_event=_futures(), futures_symbol="NIFTY-I",
        option_candidates=candidates, option_quote_events=quotes,
        causal_beta=1.15, futures_lot_size=65,
        available_capital=50_000_000.0, hedge_margin_observed=1_000.0,
        execution_timestamp_ms=T1_MS,
    )
    return opportunity_id, result


# ─── the production path records the evidence ────────────────────────────────

def test_a_real_entry_attempt_produces_an_execution_contract_record(collector, cfg):
    candidate = _candidate(25000)
    opportunity_id, _ = _enter(collector, cfg, [candidate], {candidate.symbol: _quote(candidate.symbol)})

    record = collector.last_execution_contract

    assert record is not None, "execute_pending_entry must record the execution contract"
    assert record.opportunity_id == opportunity_id
    assert record.candidates_considered == 1


def test_the_chosen_contract_is_the_one_recorded(collector, cfg):
    """Closest eligible delta to target wins, and that is what gets written."""
    near = _candidate(25000, delta=0.71)
    far = _candidate(24000, delta=0.45)
    quotes = {c.symbol: _quote(c.symbol) for c in (near, far)}

    _enter(collector, cfg, [far, near], quotes)
    record = collector.last_execution_contract

    assert record.selected is True
    assert record.executed_strike == 25000.0
    assert record.executed_delta == pytest.approx(0.71)
    assert record.candidates_considered == 2


def test_rejected_candidates_are_recorded_with_their_reasons(collector, cfg):
    """"No trade" is uninformative; "every candidate failed on OI" is a finding."""
    thin = _candidate(25000)
    _enter(collector, cfg, [thin], {thin.symbol: _quote(thin.symbol, oi=10)})

    record = collector.last_execution_contract

    assert record.candidates_eligible == 0
    assert record.selected is False
    assert record.reason == NO_ELIGIBLE_CANDIDATE
    reasons = record.candidates[0].rejection_reasons
    assert any("OI" in r for r in reasons), reasons


def test_a_wide_spread_is_recorded_as_the_rejection_reason(collector, cfg):
    wide = _candidate(25000)
    _enter(collector, cfg, [wide], {wide.symbol: _quote(wide.symbol, bid=400.0, ask=460.0)})

    record = collector.last_execution_contract

    assert record.selected is False
    assert any("SPREAD" in r for r in record.candidates[0].rejection_reasons)


def test_a_cheap_premium_is_recorded_as_the_rejection_reason(collector, cfg):
    cheap = _candidate(25000)
    _enter(collector, cfg, [cheap], {cheap.symbol: _quote(cheap.symbol, bid=1.0, ask=1.01)})

    record = collector.last_execution_contract

    assert record.selected is False
    assert any("PREMIUM" in r for r in record.candidates[0].rejection_reasons)


def test_a_candidate_with_no_quote_is_not_eligible(collector, cfg):
    blind = _candidate(25000)
    _enter(collector, cfg, [blind], {})

    record = collector.last_execution_contract

    assert record.candidates_eligible == 0
    assert record.candidates[0].best_bid is None


def test_an_empty_chain_still_records_the_attempt(collector, cfg):
    _enter(collector, cfg, [], {})

    record = collector.last_execution_contract

    assert record is not None
    assert record.candidates_considered == 0
    assert record.selected is False


# ─── theoretical target versus execution contract ────────────────────────────

def test_the_frozen_rules_are_recorded_beside_the_choice(collector, cfg):
    candidate = _candidate(25000)
    _enter(collector, cfg, [candidate], {candidate.symbol: _quote(candidate.symbol)})

    record = collector.last_execution_contract

    assert record.theoretical_delta_target == cfg.target_delta
    assert record.min_dte == cfg.min_dte
    assert record.max_dte == cfg.max_dte
    assert record.max_spread_pct == cfg.max_spread_pct
    assert record.min_option_oi == cfg.min_option_oi


def test_the_delta_gap_measures_intention_against_reality(collector, cfg):
    candidate = _candidate(25000, delta=0.64)
    _enter(collector, cfg, [candidate], {candidate.symbol: _quote(candidate.symbol)})

    record = collector.last_execution_contract

    # The frozen rule wanted 0.70; the market offered 0.64.
    assert record.delta_gap == pytest.approx(0.64 - cfg.target_delta, abs=1e-9)


def test_evidence_failure_never_breaks_an_entry(collector, cfg, monkeypatch):
    """Evidence is worth a lot, and never more than an open position."""
    import app.services.snapback_execution_contract as mod

    def explode(**_kw):
        raise RuntimeError("evidence subsystem down")

    monkeypatch.setattr(mod, "build_execution_contract_record", explode)

    candidate = _candidate(25000)
    _, result = _enter(collector, cfg, [candidate], {candidate.symbol: _quote(candidate.symbol)})

    # The entry completed and opened a position while the evidence subsystem was
    # throwing. The evidence is absent rather than silently wrong.
    assert result["status"] == "OPEN_POSITION"
    assert collector.last_execution_contract is None


# ─── the record cannot misrepresent the rule ─────────────────────────────────

def test_an_ineligible_candidate_can_never_be_recorded_as_chosen():
    from app.services.snapback_execution_contract import (
        CandidateEvaluation, build_execution_contract_record,
    )

    rejected = CandidateEvaluation(
        symbol="X", expiry="2026-10-29", strike=25000.0, option_type="PE",
        dte=45, is_monthly=True, theoretical_delta=0.7, instrument_token="1",
        lot_size=65, eligible=False, rejection_reasons=("INSUFFICIENT_OI",),
        best_bid=1.0, best_ask=1.1, spread_pct=9.0, open_interest=1,
        delta_distance=0.0,
    )

    with pytest.raises(ExecutionContractError):
        build_execution_contract_record(
            opportunity_id="O", candidates=[rejected], chosen=rejected,
            theoretical_strike=25000.0, theoretical_expiry="2026-10-29",
            target_delta=0.70, min_dte=40, max_dte=60, max_spread_pct=2.0,
            min_option_premium=10.0, min_option_oi=50_000,
            runtime_sha="a", strategy_sha="b", config_hash="c", rule_hash="d",
            evaluated_at=T1,
        )
