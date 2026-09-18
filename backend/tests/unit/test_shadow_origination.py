"""The engine now writes shadow records — and still cannot place an order.

The previous release shipped the shadow service with nothing calling it. These
tests are about the wiring: that an entry decision produces a record, that an
unreadable book is recorded as unknown rather than as an empty one, and that
the path cannot reach a broker even if someone hands it a full client.
"""
from __future__ import annotations

import pytest

from app.core.execution_vehicle import ExecutionVehicle
from app.services.kite_engine import shadow_origination
from app.services.shadow_execution import ShadowExecutionService, ShadowStore


class _Reader:
    """A market reader with a book and no way to send anything."""

    def __init__(self, quote=None, boom: Exception | None = None):
        self._quote = quote
        self._boom = boom

    async def get_quote(self, instruments):
        if self._boom:
            raise self._boom
        return {instruments[0]: self._quote} if self._quote else {}


class _ClientThatCanTrade(_Reader):
    async def place_order(self, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("the shadow path reached a broker order method")


_QUOTE = {
    "last_price": 101.5,
    "depth": {"buy": [{"price": 101.0, "quantity": 1200}],
              "sell": [{"price": 102.0, "quantity": 900}]},
}


@pytest.fixture()
def service(tmp_path):
    return ShadowExecutionService(market_reader=_Reader(_QUOTE),
                                  store=ShadowStore(tmp_path))


class TestTheBookReading:
    def test_best_bid_and_offer_are_read_from_depth(self):
        book = shadow_origination.book_from_quote(_QUOTE, observed_at="2026-09-18T10:00:00Z")
        assert (book.bid, book.ask) == (101.0, 102.0)
        assert (book.bid_qty, book.ask_qty) == (1200, 900)
        assert book.last == 101.5

    def test_a_missing_quote_is_no_book_rather_than_an_empty_one(self):
        assert shadow_origination.book_from_quote(None, observed_at="x") is None

    def test_a_quote_with_no_depth_leaves_the_sizes_unknown(self):
        book = shadow_origination.book_from_quote(
            {"last_price": 50.0}, observed_at="2026-09-18T10:00:00Z")
        # Not zero: nobody looked at the depth, which is not the same as nobody bidding.
        assert book.bid is None and book.bid_qty is None
        assert book.last == 50.0


class TestRecordingAnIntent:
    @pytest.mark.asyncio
    async def test_an_entry_decision_is_recorded_against_the_observed_book(self, service, tmp_path):
        record = await shadow_origination.record_entry_intent(
            client=_Reader(_QUOTE), lane_key="supertrend:swing", symbol="NIFTY26JAN24000CE",
            exchange="NFO", quantity=75, vehicle=ExecutionVehicle.OPTIONS_LONG,
            reference_price=101.5, margin_available=500_000.0,
            protection_feasible=True, service=service)

        assert record is not None
        assert record.lane_key == "supertrend:swing"
        assert record.intended_quantity == 75
        stored = service.store.read(record.session_date)
        assert len(stored) == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_book_still_records_the_intent(self, tmp_path):
        service = ShadowExecutionService(
            market_reader=_Reader(boom=RuntimeError("quote service down")),
            store=ShadowStore(tmp_path))
        record = await shadow_origination.record_entry_intent(
            client=_Reader(), lane_key="supertrend:swing", symbol="NIFTY26JAN24000CE",
            exchange="NFO", quantity=75, vehicle=ExecutionVehicle.OPTIONS_LONG,
            reference_price=101.5, service=service)

        # The intent is evidence even when the market could not be read: what is
        # refused is a claim about fillability, not the record of the decision.
        assert record is not None
        assert record.observed_at_selection is None

    @pytest.mark.asyncio
    async def test_a_zero_quantity_intent_is_refused_not_recorded(self, service):
        record = await shadow_origination.record_entry_intent(
            client=_Reader(_QUOTE), lane_key="supertrend:swing", symbol="X",
            exchange="NFO", quantity=0, vehicle=ExecutionVehicle.OPTIONS_LONG,
            reference_price=1.0, service=service)
        assert record is None

    @pytest.mark.asyncio
    async def test_recording_never_raises_into_the_caller(self, service, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(service, "record", _boom)
        record = await shadow_origination.record_entry_intent(
            client=_Reader(_QUOTE), lane_key="supertrend:swing", symbol="X",
            exchange="NFO", quantity=75, vehicle=ExecutionVehicle.OPTIONS_LONG,
            reference_price=1.0, service=service)
        # A failure to record evidence must not cancel a decision already made.
        assert record is None


class TestTheShadowPathCannotTrade:
    @pytest.mark.asyncio
    async def test_a_client_that_can_place_orders_is_wrapped_not_handed_over(self, tmp_path):
        """The reader the service receives must expose no order method at all."""
        client = _ClientThatCanTrade(_QUOTE)
        record = await shadow_origination.record_entry_intent(
            client=client, lane_key="supertrend:swing", symbol="NIFTY26JAN24000CE",
            exchange="NFO", quantity=75, vehicle=ExecutionVehicle.OPTIONS_LONG,
            reference_price=101.5,
            service=ShadowExecutionService(
                market_reader=shadow_origination._MarketReader(client),
                store=ShadowStore(tmp_path)))
        assert record is not None

    def test_the_service_refuses_a_raw_trading_client(self, tmp_path):
        from app.services.shadow_execution import OrderCapabilityError

        with pytest.raises(OrderCapabilityError):
            ShadowExecutionService(market_reader=_ClientThatCanTrade(_QUOTE),
                                   store=ShadowStore(tmp_path))

    def test_the_wrapper_exposes_only_reads(self):
        wrapper = shadow_origination._MarketReader(_ClientThatCanTrade(_QUOTE))
        assert not hasattr(wrapper, "place_order")
        assert hasattr(wrapper, "get_quote")


class TestTheEngineActuallyWritesOne:
    """The gap the previous release left open: nothing called the service."""

    @pytest.mark.asyncio
    async def test_the_auto_exec_entry_path_records_a_shadow_intent(self, monkeypatch, tmp_path):
        import time

        from app.services.kite_engine import service as ksvc

        seen: list[dict] = []

        async def _spy(**kwargs):
            seen.append(kwargs)
            return None

        from app.services.kite_engine import shadow_origination

        monkeypatch.setattr(shadow_origination, "record_entry_intent", _spy)

        # Drive the same callback the scanner uses, with the double from the
        # engine suite, and assert the entry decision produced an intent.
        from tests.engines.sterling_kite_engine.canonical_double import CanonicalBrokerDouble
        from app.engines.sterling_kite_engine.schemas import (
            AlignmentChip, EngineSignalRow, OptionLeg,
        )
        from app.services.kite_engine import state

        state.reset("shadow-uid")

        class _Client(CanonicalBrokerDouble):
            async def place_order_option(self, sym, side, size, **kw):
                return {"order_id": "O-1"}

            async def get_ltp(self, keys):
                return {k: {"last_price": 90.0} for k in keys}

        row = EngineSignalRow(
            underlying="RELIANCE", token=111, exchange="NFO", regime="BULL",
            alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long",
            option_type="CE",
            legs=[OptionLeg(moneyness="ATM", option_type="CE",
                            option_symbol="RELIANCE25JUN3000CE", strike=3000,
                            expiry="2026-06-26", lot_size=250)],
            spot=3010.0, stop_loss=2950.0, score=85.0,
            # The engine refuses a signal that is not one closed bar old, so the
            # timestamp has to be a realistic one rather than a placeholder.
            timestamp_ms=int((time.time() - 3600) * 1000))

        await ksvc._make_place_cb(_Client(), "shadow-uid")(row, None)

        assert seen, "the entry path recorded no shadow intent"
        assert seen[0]["symbol"] == "RELIANCE25JUN3000CE"
        assert seen[0]["lane_key"].startswith("supertrend:")
        assert seen[0]["quantity"] > 0
