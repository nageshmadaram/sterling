"""Shadow evidence: a no-fill is a result, and must stay one.

The central test is the constructor guard. Any path that lets a NO_FILL carry a
price or a quantity turns the whole shadow phase into a second paper run.
"""
from __future__ import annotations

import pytest

from app.core.shadow_record import (
    BookObservation,
    RefusalReason,
    ShadowOutcome,
    ShadowRecord,
    render_shadow_metrics,
    shadow_lane_key,
    summarize_all,
    summarize_lane,
)


def _book(observed_at="2026-09-18T09:20:05+05:30", bid=100.0, ask=101.0, ask_qty=75):
    return BookObservation(
        observed_at=observed_at, bid=bid, ask=ask, bid_qty=75, ask_qty=ask_qty
    )


def _record(**overrides):
    row = {
        "lane_key": "snapback:scalping",
        "session_date": "2026-09-18",
        "signal_at": "2026-09-18T09:20:00+05:30",
        "contract": "NIFTY26SEP25000CE",
        "selected_at": "2026-09-18T09:20:03+05:30",
        "intended_quantity": 75,
        "observed_at_selection": _book(),
        "broker_margin": 12000.0,
        "protection_feasible": True,
        "outcome": ShadowOutcome.FILLED,
        "filled_quantity": 75,
        "hypothetical_fill_price": 101.0,
        "reference_price": 100.5,
    }
    row.update(overrides)
    return ShadowRecord(**row)


class TestNoSyntheticFills:
    def test_a_no_fill_may_not_carry_a_price(self):
        with pytest.raises(ValueError, match="must not carry a fill"):
            _record(
                outcome=ShadowOutcome.NO_FILL,
                filled_quantity=0,
                hypothetical_fill_price=101.0,
            )

    def test_a_no_fill_may_not_carry_a_quantity(self):
        with pytest.raises(ValueError, match="must not carry a fill"):
            _record(
                outcome=ShadowOutcome.NO_FILL,
                filled_quantity=75,
                hypothetical_fill_price=None,
            )

    def test_a_clean_no_fill_is_accepted(self):
        row = _record(
            outcome=ShadowOutcome.NO_FILL,
            filled_quantity=0,
            hypothetical_fill_price=None,
        )
        assert row.fill_ratio == 0.0
        assert row.slippage is None

    def test_a_fill_without_quantity_is_refused(self):
        with pytest.raises(ValueError, match="no filled quantity"):
            _record(outcome=ShadowOutcome.FILLED, filled_quantity=0)

    def test_a_refusal_must_say_why(self):
        with pytest.raises(ValueError, match="without a refusal reason"):
            _record(
                outcome=ShadowOutcome.REFUSED,
                filled_quantity=0,
                hypothetical_fill_price=None,
                refusal_reason=None,
            )


class TestDerivedFields:
    def test_slippage_is_fill_minus_reference(self):
        assert _record().slippage == pytest.approx(0.5)

    def test_selection_drift_is_measured_in_seconds(self):
        assert _record().selection_drift_seconds == pytest.approx(3.0)

    def test_drift_across_a_naive_and_aware_stamp_is_unknown_not_wrong(self):
        # Assuming a timezone here would report a drift wrong by hours.
        row = _record(selected_at="2026-09-18T09:20:03")
        assert row.selection_drift_seconds is None

    def test_depth_is_unknown_when_the_book_did_not_report_it(self):
        row = _record(observed_at_selection=_book(ask_qty=None))
        assert row.depth_sufficient is None

    def test_depth_short_of_the_requested_size_is_false(self):
        assert _record(observed_at_selection=_book(ask_qty=50)).depth_sufficient is False

    def test_spread_bps_is_relative_to_mid(self):
        book = _book(bid=100.0, ask=101.0)
        assert book.spread == pytest.approx(1.0)
        assert book.spread_bps == pytest.approx(1.0 / 100.5 * 10_000)

    def test_an_unobserved_intent_has_no_fill_ratio(self):
        row = _record(
            outcome=ShadowOutcome.UNOBSERVED,
            filled_quantity=0,
            hypothetical_fill_price=None,
        )
        assert row.fill_ratio is None


class TestMetrics:
    def _rows(self):
        return [
            _record(),
            _record(
                outcome=ShadowOutcome.PARTIAL,
                filled_quantity=25,
                hypothetical_fill_price=101.5,
            ),
            _record(
                outcome=ShadowOutcome.NO_FILL,
                filled_quantity=0,
                hypothetical_fill_price=None,
            ),
            _record(
                outcome=ShadowOutcome.REFUSED,
                filled_quantity=0,
                hypothetical_fill_price=None,
                refusal_reason=RefusalReason.NO_LISTED_CONTRACT,
            ),
            _record(
                outcome=ShadowOutcome.UNOBSERVED,
                filled_quantity=0,
                hypothetical_fill_price=None,
                observed_at_selection=None,
                broker_margin=None,
                protection_feasible=None,
            ),
        ]

    def test_an_unobserved_intent_does_not_count_as_a_no_fill(self):
        # Blaming the market for a broken feed would understate fillability.
        metrics = summarize_lane("snapback:scalping", self._rows())

        assert metrics.intents == 5
        assert metrics.measured == 3
        assert metrics.fillability_rate == pytest.approx(2 / 3)
        assert metrics.no_fill_rate == pytest.approx(1 / 3)

    def test_refusals_are_counted_by_reason(self):
        metrics = summarize_lane("snapback:scalping", self._rows())
        assert metrics.refused == 1
        assert metrics.refusals_by_reason == {"no_listed_contract": 1}
        assert metrics.contracts_unavailable == 1
        assert metrics.refusal_rate == pytest.approx(1 / 5)

    def test_unknown_margin_and_protection_are_counted_not_assumed(self):
        metrics = summarize_lane("snapback:scalping", self._rows())
        assert metrics.margin_unknown == 1
        assert metrics.protection_unknown == 1

    def test_rates_are_none_when_nothing_was_measured(self):
        metrics = summarize_lane("snapback:swing", [])
        assert metrics.fillability_rate is None
        assert metrics.no_fill_rate is None
        assert metrics.refusal_rate is None

    def test_lanes_are_summarised_separately(self):
        rows = [
            _record(),
            _record(lane_key="supertrend:intraday"),
            _record(lane_key="supertrend:intraday"),
        ]
        metrics = summarize_all(rows)

        assert set(metrics) == {"snapback:scalping", "supertrend:intraday"}
        assert metrics["snapback:scalping"].intents == 1
        assert metrics["supertrend:intraday"].intents == 2

    def test_render_shows_unknowns_as_question_marks(self):
        out = render_shadow_metrics({"snapback:swing": summarize_lane("snapback:swing", [])})
        assert "snapback:swing" in out
        assert "?" in out


def test_lane_key_canonicalises_a_legacy_mode():
    assert shadow_lane_key("snapback", "scalp") == "snapback:scalping"
