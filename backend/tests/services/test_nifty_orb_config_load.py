"""Config load-time safety and single-auto-path guarantees."""
import json
from datetime import date

import pytest

from app.services import nifty_orb_options as service
from app.engines.nifty_orb_options import StrategyConfig, is_monthly_expiry


class FakeDB:
    def __init__(self, stored=None):
        self.store = dict(stored or {})

    def get_config(self, key):
        return self.store.get(key)

    def set_config(self, key, value):
        self.store[key] = value


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    import app.services.db as real_db
    monkeypatch.setattr(real_db, "get_config", fake.get_config)
    monkeypatch.setattr(real_db, "set_config", fake.set_config)
    return fake


def test_a_valid_stored_config_is_loaded(db):
    db.store[service._CONFIG_KEY] = json.dumps({**StrategyConfig().__dict__, "enabled": True, "max_risk_inr": 5000.0})
    cfg = service.get_config()
    assert cfg.enabled is True
    assert cfg.max_risk_inr == 5000.0


def test_an_invalid_stored_config_falls_back_to_disabled_defaults(db, caplog):
    """A row written before validation existed must not become a trading config."""
    db.store[service._CONFIG_KEY] = json.dumps({**StrategyConfig().__dict__, "enabled": True, "volume_multiplier": 0.0})
    cfg = service.get_config()
    assert cfg.enabled is False                      # disabled is the safe state
    assert cfg == StrategyConfig(enabled=False)
    assert "invalid" in caplog.text.lower()


def test_an_out_of_range_stored_dte_range_also_falls_back(db):
    db.store[service._CONFIG_KEY] = json.dumps(
        {**StrategyConfig().__dict__, "enabled": True, "expiry_dte_min": 9, "expiry_dte_max": 2}
    )
    assert service.get_config().enabled is False


def test_no_stored_config_means_the_shipped_defaults(db):
    """Nothing stored means the defaults, whatever they are.

    Fresh install is engine OFF. The safety case — a stored config that will
    not validate — is asserted separately, and that one still falls back OFF.
    """
    assert service.get_config() == StrategyConfig()
    assert service.get_config().enabled is False


def test_an_invalid_stored_config_still_falls_back_off(db):
    """The fallback that is a safety device, as opposed to a default."""
    from app.services import db as store
    store.set_config(service._CONFIG_KEY, "{not json")
    assert service.get_config().enabled is False


def test_set_config_persists_only_a_validated_config(db):
    with pytest.raises(ValueError, match="volume_multiplier"):
        service.set_config({"volume_multiplier": 0})
    assert service._CONFIG_KEY not in db.store   # nothing half-written


def test_set_config_rejects_an_expiry_preference_the_api_does_not_offer(db):
    """The engine accepts "any"; an operator-facing options strategy must choose."""
    with pytest.raises(ValueError, match="nearest, weekly or monthly"):
        service.set_config({"expiry_selection": "any"})


def test_execute_scan_is_the_only_automatic_execution_path():
    """`execute_auto` was an unreferenced duplicate that skipped the daily-loss
    breaker, fabricated a full fill on a broker read failure, and ignored whether
    protection actually armed. Any re-introduction must go through execute_scan."""
    assert not hasattr(service, "execute_auto")
    from app.services import nifty_orb_execution
    assert callable(nifty_orb_execution.execute_scan)


@pytest.mark.asyncio
async def test_execute_manual_does_not_place_an_order(monkeypatch, db):
    """The /execute endpoint used to call place_manual_order on the snapshot
    without protection. Manual Buy is the board ticket."""
    placed = []

    async def boom(*a, **k):
        placed.append(1)
        raise AssertionError("execute_manual must not place")

    monkeypatch.setattr(service, "get_config", lambda: StrategyConfig(enabled=False))
    monkeypatch.setattr("app.services.kite_engine.service.place_manual_order", boom, raising=False)
    out = await service.execute_manual("u1")
    assert out["status"] == "manual"
    assert out["executed"] == []
    assert placed == []


@pytest.mark.asyncio
async def test_execute_manual_returns_the_same_ticket_without_placing(monkeypatch, db):
    placed = []

    async def boom(*a, **k):
        placed.append(1)
        raise AssertionError("execute_manual must not place")

    plan = {
        "quantity": 75,
        "stop_premium": 14.0,
        "target_premium": 26.0,
        "underlying_entry": 25000.0,
        "contract": {
            "symbol": "NIFTY26AUG25000CE",
            "option_type": "CE",
            "strike": 25000,
            "expiry": "2026-08-27",
            "lot_size": 75,
        },
    }
    signal = {"direction": "LONG", "timestamp": "2026-08-25T10:30:00+05:30"}

    async def fake_scan(uid, cfg=None):
        return {
            "signals": [{
                "status": "signal",
                "underlying": "BANKNIFTY",
                "trade": plan,
                "signal": signal,
            }],
        }

    monkeypatch.setattr(service, "get_config", lambda: StrategyConfig(enabled=True))
    monkeypatch.setattr("app.services.nifty_orb_scanner.scan_user", fake_scan)
    monkeypatch.setattr("app.services.kite_engine.service.place_manual_order", boom, raising=False)
    out = await service.execute_manual("u1")
    assert out["status"] == "manual"
    assert out["ticket"]["symbol"] == "NIFTY26AUG25000CE"
    assert out["ticket_fingerprint"]
    assert out["signals"][0]["underlying"] == "BANKNIFTY"
    assert placed == []


def test_the_kite_expiry_rule_is_the_engine_rule():
    """Guards against a third local reimplementation of weekly-vs-monthly."""
    import inspect
    source = inspect.getsource(service._kite_options)
    assert "is_monthly_expiry" in source
    assert is_monthly_expiry(date(2026, 8, 27)) is True      # last Thursday of August
    assert is_monthly_expiry(date(2026, 9, 3)) is False


def test_orb_owns_no_trailing_knob(db):
    """`trail_atr` was declared, validated, API-settable and documented as
    "Trail: 1.25 ATR" -- and read by nothing. Trailing comes from the universal
    trading mode's `trail_atr_mult`. A config field nothing honours is a claim
    about behaviour the strategy does not have."""
    assert "trail_atr" not in StrategyConfig.__dataclass_fields__
    with pytest.raises(ValueError, match="Unknown NIFTY ORB config fields"):
        service.set_config({"trail_atr": 1.25})


def test_the_surviving_risk_knobs_are_all_honoured(db):
    """Guard against another write-only field: every field must be read somewhere."""
    import inspect
    from app.engines import nifty_orb_options as engine
    from app.services import nifty_orb_execution, nifty_orb_scanner
    sources = "".join(inspect.getsource(m) for m in (engine, nifty_orb_execution, nifty_orb_scanner, service))
    exempt = {"enabled", "underlying", "execution_broker"}   # read by callers/UI, not the math
    unread = [
        name for name in StrategyConfig.__dataclass_fields__
        if name not in exempt and f".{name}" not in sources and f'"{name}"' not in sources
    ]
    assert unread == [], f"config fields nothing reads: {unread}"
