"""The production order path: durability, the safety gate, and broker truth.

These are the cases that only bite in production — a restart while holding, a
fill that came in worse than the limit, a retry after a timeout — and none of
them show up in a happy-path run.
"""
from __future__ import annotations

import pytest

from app.engines.gamma_move import (GammaMoveConfig, GammaSignal, InstrumentRef,
                                    PositionState, TradeRecord)
from app.services import gamma_move_positions as store
from tests.engines.gamma_move.conftest import BASE_MS


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "t.db"))
    from app.services import db
    from app.services import gamma_move_runner as runner
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    db.init()
    store.reset()
    runner.clear()
    yield
    store.reset()
    runner.clear()


def inst(symbol="RELIANCE26SEP1300CE") -> InstrumentRef:
    return InstrumentRef(instrument_id="12345", tradingsymbol=symbol, option_type="CE",
                         strike=1300.0, expiry="2026-09-29", lot_size=500, tick_size=0.05)


def pos(**kw) -> PositionState:
    base = dict(signal_id="s1", instrument=inst(), entry=53.0, stop=45.0, quantity=500,
                lots=1, entered_ms=1, entry_day="2026-09-20", order_id="o1")
    base.update(kw)
    return PositionState(**base)


class TestDurability:
    def test_a_position_survives_a_restart(self):
        """The whole point. A crash while long must not lose the position — the
        process that comes back would never exit something it cannot see."""
        store.put("u1", pos())
        store.reset()                              # simulate the restart
        back = store.get("u1", "RELIANCE26SEP1300CE")
        assert back is not None
        assert back.entry == 53.0 and back.stop == 45.0 and back.quantity == 500
        assert back.instrument.tradingsymbol == "RELIANCE26SEP1300CE"

    def test_open_positions_excludes_closed_ones(self):
        store.put("u1", pos())
        assert len(store.open_positions("u1")) == 1
        store.close("u1", "RELIANCE26SEP1300CE", "stop")
        assert store.open_positions("u1") == []

    def test_one_unreadable_row_does_not_lose_the_rest(self, monkeypatch):
        store.put("u1", pos())
        store.put("u1", pos(instrument=inst("OTHER26SEP1CE")))
        import json
        from app.services import db
        raw = json.loads(db.get_config("gamma_move_positions_u1"))
        raw.append({"garbage": True})
        db.set_config("gamma_move_positions_u1", json.dumps(raw))
        store.reset()
        assert len(store.load("u1")) == 2


class TestBrokerTruth:
    def test_a_sent_order_is_not_yet_a_position(self):
        p = store.put("u1", pos())
        assert p.status == "pending"
        assert p.is_open                          # tracked, but not confirmed

    def test_a_worse_fill_moves_the_stop_with_it(self):
        """The stop was sized against the intended entry. Leaving it where it was
        silently widens the risk past what the sizer allowed."""
        store.put("u1", pos(entry=53.0, stop=45.0))
        p = store.mark_filled("u1", "RELIANCE26SEP1300CE", 56.0)
        assert p.fill_price == 56.0
        assert p.stop == 48.0                      # +3 drift carried onto the stop
        assert p.effective_entry == 56.0
        assert p.status == "open"

    def test_effective_entry_falls_back_to_the_intended_price(self):
        assert store.put("u1", pos()).effective_entry == 53.0

    def test_a_rejection_is_recorded_not_silently_dropped(self):
        store.put("u1", pos())
        p = store.mark_rejected("u1", "RELIANCE26SEP1300CE", "REJECTED")
        assert p.status == "rejected" and not p.is_open


class TestSafetyGate:
    def test_the_kill_switch_stops_an_entry(self, monkeypatch):
        from app.services import gamma_move_runner as runner
        monkeypatch.setattr("app.services.live_safety.kill_switch_state",
                            lambda: {"enabled": True, "reason": "manual halt"})
        ok, why = runner._safety("u1", "key-1")
        assert ok is False and "Kill switch" in why

    def test_it_fails_closed_when_it_cannot_be_evaluated(self, monkeypatch):
        """An unavailable safety check is not a passed one."""
        from app.services import gamma_move_runner as runner
        def boom(*a, **k):
            raise RuntimeError("safety subsystem down")
        monkeypatch.setattr("app.services.live_safety.assert_safe_to_trade", boom)
        ok, why = runner._safety("u1", "key-1")
        assert ok is False and "unavailable" in why

    def test_a_duplicate_key_is_refused(self, monkeypatch):
        from app.services import gamma_move_runner as runner
        monkeypatch.setattr("app.services.live_safety.check_idempotency",
                            lambda key: "ORDER-1")
        ok, why = runner._safety("u1", "key-1")
        assert ok is False and "Duplicate" in why


class TestModeIsReadNotStored:
    def test_config_carries_no_mode_fields(self):
        names = GammaMoveConfig.field_names()
        assert "execution_mode" not in names
        assert "protection_mode" not in names

    def test_is_paper_defaults_safe_without_an_account(self):
        from app.services import gamma_move_runner as runner
        assert runner.is_paper("nobody") is True
        assert runner.auto_execute("nobody") is False

    def test_is_paper_follows_the_account(self, monkeypatch):
        from app.services import gamma_move_runner as runner

        class Acct:
            is_paper = False
        monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active",
                            lambda uid: Acct())
        assert runner.is_paper("u1") is False


class TestDefaultsDoNotOverrideIntent:
    """Changing a default must not flip a config someone deliberately set.

    `enabled` moved from off to on. An operator who had turned this engine off
    must find it still off after that change — a default is what applies when
    nobody has said otherwise, not a way to overrule them on upgrade.
    """

    def test_a_stored_off_state_survives_the_new_default(self):
        import json
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", json.dumps({"enabled": False}))
        assert get_config().enabled is False

    def test_fields_never_stored_still_take_the_current_default(self):
        import json
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", json.dumps({"enabled": False}))
        cfg = get_config()
        assert cfg.stop_mode == "both"
        assert cfg.scan_all_stocks is True

    def test_an_unset_config_is_on(self):
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", "")
        assert get_config().enabled is True

    def test_an_unreadable_config_falls_back_OFF(self):
        """The one case where a fallback should overrule the default: a config
        that will not validate must not become a trading config."""
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", "{not json at all")
        assert get_config().enabled is False

    def test_an_invalid_stored_config_falls_back_OFF(self):
        import json
        from app.services import db
        from app.services.gamma_move import get_config
        db.set_config("gamma_move_config", json.dumps({"min_oi_drop_pct": 0}))
        assert get_config().enabled is False


class TestReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_handles_list_of_account_positions(self, monkeypatch):
        from app.services import gamma_move_runner
        from app.schemas.account import AccountPosition

        class MockClient:
            async def get_positions(self):
                return [
                    AccountPosition(
                        symbol="NIFTY26SEP25000CE",
                        underlying="NFO:NIFTY",
                        size=50.0,
                        side="long",
                        entry_price=120.0,
                        mark_price=130.0,
                        unrealized_pnl=500.0,
                        realized_pnl=0.0,
                        margin=6000.0,
                        position_type="MIS",
                    )
                ]

        async def _mock_client(uid):
            return MockClient()

        monkeypatch.setattr(gamma_move_runner, "_client", _mock_client)
        monkeypatch.setattr(gamma_move_runner, "is_paper", lambda uid: True)

        res = await gamma_move_runner.reconcile("default")
        assert res is not None

        orphans = await gamma_move_runner.orphan_positions("default", gamma_move_runner.get_config())
        assert len(orphans) == 1
        assert orphans[0]["symbol"] == "NIFTY26SEP25000CE"
        assert orphans[0]["quantity"] == 50
        assert orphans[0]["entry_price"] == 120.0


class TestSessionReuse:
    def test_session_for_reuses_the_persisted_record_across_midnight(self, monkeypatch):
        """A new Session at midnight would drop descale and the daily loss tally."""
        from app.services import gamma_move_runner as runner
        rec = TradeRecord(descale_step=2, trades=6, losses=6,
                          day="2026-09-08", day_realised_inr=-4000.0)
        store.save_record("u1", rec)
        monkeypatch.setattr(runner, "_today_str", lambda: "2026-09-08")
        s1 = runner.session_for("u1", GammaMoveConfig())
        assert s1.strategy.state.record.descale_step == 2
        monkeypatch.setattr(runner, "_today_str", lambda: "2026-09-09")
        s2 = runner.session_for("u1", GammaMoveConfig())
        assert s2 is s1
        assert s2.strategy.state.record is s1.strategy.state.record
        assert s2.strategy.state.record.descale_step == 2
        assert s2.strategy.state.day == "2026-09-09"

    def test_load_record_rebuilds_descale_from_dict(self):
        rec = TradeRecord.from_dict({
            "descale_step": 2, "trades": 6, "losses": 6,
            "day": "2026-09-08", "day_realised_inr": -4000,
        })
        assert rec.descale_step == 2
        assert rec.descaled is True
        store.save_record("u1", rec)
        store.reset()
        back = store.load_record("u1")
        assert back.descale_step == 2
        assert back.descaled is True
        assert back.day == "2026-09-08"


class TestLiveSpreadGate:
    def test_spread_of_3_1_percent_is_refused(self):
        from app.services import gamma_move_runner as runner
        cfg = GammaMoveConfig()
        wide = {"depth": {"buy": [{"price": 100.0}], "sell": [{"price": 103.15}]}}
        msg = runner.spread_blocks_entry(wide, cfg)
        assert msg is not None
        assert "spread" in msg.lower()

    def test_spread_inside_3_percent_is_allowed(self):
        from app.services import gamma_move_runner as runner
        cfg = GammaMoveConfig()
        tight = {"depth": {"buy": [{"price": 100.0}], "sell": [{"price": 102.9}]}}
        assert runner.spread_blocks_entry(tight, cfg) is None

    @pytest.mark.asyncio
    async def test_arm_refuses_a_wide_live_quote(self, monkeypatch):
        from app.engines.gamma_move import SpotLevel, StrikeCandidate
        from app.services import gamma_move_runner as runner

        cfg = GammaMoveConfig()
        instrument = inst()
        cand = StrikeCandidate(
            underlying="RELIANCE",
            level=SpotLevel(price=1300.0, kind="resistance", touches=3),
            instrument=instrument, oi=6_000_000, days_to_expiry=9, spot=1298.0,
            premium=53.0, chain_oi_max=6_000_000)
        sig = GammaSignal(id="s-wide", candidate=cand, metrics=None, state="armed",
                          at_ms=BASE_MS, regime="up", entry=53.0, stop=45.0,
                          lots=1, quantity=500)
        session = runner.session_for("u1", cfg)
        session.signals[sig.id] = sig

        class Client:
            async def get_quote(self, keys):
                return {"NFO:RELIANCE26SEP1300CE": {
                    "depth": {"buy": [{"price": 50.0}], "sell": [{"price": 52.0}]},
                }}

            async def place_order(self, *a, **k):
                raise AssertionError("must not place when the quote is wide")

        async def _client(_uid):
            return Client()

        monkeypatch.setattr(runner, "_client", _client)
        result = await runner.arm("u1", sig.id)
        assert result["ok"] is False
        assert "spread" in result["message"].lower()

