"""Sterling's journal being empty is not the broker being flat.

Written after a live check found 16625 of CDSL26SEP1500CE open on the family
account with no Sterling intent, no fill, no registry entry and no protection —
while every local store read as flat. These tests keep that reading impossible.
"""
from __future__ import annotations

import pytest

from app.services.external_positions import (
    DISCOVERY_REASON,
    SOURCE,
    ExternalExposure,
    classify_broker_positions,
    external_exposure,
    render_external,
)

ACCOUNT = "AA0595"


def _broker_row(symbol="CDSL26SEP1500CE", quantity=16625, **overrides):
    row = {
        "tradingsymbol": symbol, "exchange": "NFO", "product": "NRML",
        "quantity": quantity, "average_price": 3.271429, "last_price": 3.35,
        "unrealised": 1306.24,
    }
    row.update(overrides)
    return row


class TestClassification:
    def test_a_position_sterling_never_opened_is_external(self):
        exposure = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is False
        position = exposure.positions[0]
        assert position.instrument == "CDSL26SEP1500CE"
        assert position.quantity == 16625
        assert position.source == SOURCE
        assert position.discovery_reason == DISCOVERY_REASON

    def test_its_provenance_says_sterling_did_not_open_it(self):
        position = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=set()).positions[0]
        # Importing it as though Sterling had opened it would put a trade no
        # strategy made into the promotion sample.
        assert position.managed_by_sterling is False
        assert position.sterling_intent == "none"
        assert position.sterling_fill == "none"
        assert position.protection_known is False

    def test_a_position_sterling_knows_about_is_not_external(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="NIFTY26JAN24000CE")], account=ACCOUNT,
            known_symbols={"NIFTY26JAN24000CE"})
        assert exposure.flat is True

    def test_matching_ignores_case(self):
        exposure = classify_broker_positions(
            [_broker_row(symbol="nifty26jan24000ce")], account=ACCOUNT,
            known_symbols={"NIFTY26JAN24000CE"})
        assert exposure.flat is True

    def test_a_closed_position_is_not_exposure(self):
        exposure = classify_broker_positions(
            [_broker_row(quantity=0)], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is True

    def test_a_short_position_is_still_exposure(self):
        exposure = classify_broker_positions(
            [_broker_row(quantity=-50)], account=ACCOUNT, known_symbols=set())
        assert exposure.flat is False
        assert exposure.positions[0].quantity == -50


class TestUnknownIsNeverFlat:
    def test_unreadable_local_state_refuses_to_classify_anything(self):
        # If Sterling cannot say what it knows, no broker position can be
        # attributed OR excluded.
        exposure = classify_broker_positions(
            [_broker_row()], account=ACCOUNT, known_symbols=None)
        assert exposure.readable is False
        assert exposure.flat is None
        assert exposure.count is None

    @pytest.mark.asyncio
    async def test_an_unreachable_broker_is_unknown_not_flat(self):
        class _Client:
            async def get_positions_raw(self):
                raise TimeoutError()

        exposure = await external_exposure(_Client(), account=ACCOUNT)
        assert exposure.flat is None
        # A bare TimeoutError stringifies to nothing; the type must survive.
        assert "TimeoutError" in exposure.detail

    @pytest.mark.asyncio
    async def test_no_live_account_is_unknown_not_flat(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.all_accounts", lambda: [])
        monkeypatch.setattr(
            "app.services.exchanges.kite.accounts.bootstrap", lambda: None)
        exposure = await external_exposure()
        assert exposure.flat is None

    def test_the_rendering_says_unknown_rather_than_none(self):
        text = render_external(ExternalExposure(readable=False, detail="gateway down"))
        assert "UNKNOWN" in text and "gateway down" in text


class TestItReachesTheGates:
    def test_the_snapshot_total_includes_external_exposure(self, monkeypatch):
        from app.services import exposure_snapshot as module

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])
        monkeypatch.setattr(module, "_external",
                            lambda: (1, "", ("CDSL26SEP1500CE",), None))
        snapshot = module.exposure_snapshot()
        assert snapshot.total == 1

    def test_an_unknown_broker_makes_the_total_unknown(self, monkeypatch):
        from app.services import exposure_snapshot as module

        monkeypatch.setattr("app.services.db.init", lambda *a, **k: True)
        monkeypatch.setattr("app.services.kite_engine.positions.known_uids", lambda: [])
        monkeypatch.setattr(module, "_external", lambda: (None, "timed out", (), None))
        snapshot = module.exposure_snapshot()
        assert snapshot.total is None

    def test_the_certification_gate_names_the_external_position(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services.release_certification import _open_exposure_gate

        snapshot = module.ExposureSnapshot(
            unresolved_intents=0, open_positions=0, external_positions=1,
            external_instruments=("CDSL26SEP1500CE",))
        gate = _open_exposure_gate(snapshot)
        assert gate.status == "FAIL"
        assert "CDSL26SEP1500CE" in gate.detail
        assert "did not open" in gate.detail

    def test_the_start_sequence_will_not_reach_clean(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services import service_lifecycle

        monkeypatch.setattr(
            service_lifecycle, "_set_recovery",
            lambda *a, **k: pytest.fail("recovery was cleared with exposure open"))
        monkeypatch.setattr(
            "app.services.exposure_snapshot.exposure_snapshot",
            lambda **k: module.ExposureSnapshot(
                unresolved_intents=0, open_positions=0, external_positions=1,
                external_instruments=("CDSL26SEP1500CE",)))
        result = service_lifecycle._step_clear_recovery()
        assert result.ok is False
        assert "the broker holds" in result.detail

    def test_an_unknown_exposure_also_blocks_clean(self, monkeypatch):
        from app.services import exposure_snapshot as module
        from app.services import service_lifecycle

        monkeypatch.setattr(
            service_lifecycle, "_set_recovery",
            lambda *a, **k: pytest.fail("recovery was cleared on an unknown exposure"))
        monkeypatch.setattr(
            "app.services.exposure_snapshot.exposure_snapshot",
            lambda **k: module.ExposureSnapshot(
                unresolved_intents=0, open_positions=0, external_positions=None,
                external_detail="broker timed out"))
        result = service_lifecycle._step_clear_recovery()
        assert result.ok is None
        assert "broker timed out" in result.detail

    def test_the_digest_puts_it_above_every_procedural_state(self):
        from app.services.daily_digest import Digest, _next_action

        digest = Digest(generated_at="now", recovery="FAIL: RECOVERY_REQUIRED",
                        external_exposure="1 NOT MANAGED BY STERLING: CDSL26SEP1500CE")
        # Procedural states cost time; an unwatched live position costs money
        # while the operator is reading.
        assert "Sterling did not open" in _next_action(digest)


class TestTheClientIsNotSharedBetweenLoops:
    def test_the_cache_identity_includes_the_event_loop(self):
        import inspect

        from app.services.exchanges.kite import accounts

        source = inspect.getsource(accounts.acquire_client)
        # A client cached from a closed loop fails with "Event loop is closed",
        # which a flatness check would read as "the broker is unreachable".
        assert "_running_loop_id()" in source
