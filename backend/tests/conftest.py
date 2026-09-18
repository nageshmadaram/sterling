import os
import tempfile
import numpy as np
import pytest
from typing import List
from app.schemas.market import Candle

# Ensure pytest NEVER writes to or reads from the live sterling_paper.db.
# All tests execute against an isolated temporary test database.
if "STERLING_DB_PATH" not in os.environ:
    _TEST_DB = tempfile.NamedTemporaryFile(suffix="_sterling_test.db", delete=False)
    _TEST_DB.close()
    os.environ["STERLING_DB_PATH"] = _TEST_DB.name

# An absent safety state now reads as SAFE_MODE, which is the correct production
# answer and the wrong default for a test suite: every capacity and execution test
# would refuse before reaching the behaviour it is actually asserting. So the suite
# stands up an initialised NORMAL state, in a temporary file, exactly as a real
# machine does on first boot. Tests that care about safe mode engage it themselves.
if "STERLING_SAFE_MODE_FILE" not in os.environ:
    _TEST_SAFE_MODE = tempfile.NamedTemporaryFile(
        suffix="_sterling_test_safe_mode.json", delete=False
    )
    _TEST_SAFE_MODE.close()
    os.environ["STERLING_SAFE_MODE_FILE"] = _TEST_SAFE_MODE.name
    os.unlink(_TEST_SAFE_MODE.name)
    from app.services.safe_mode import SafeModeService

    SafeModeService(_TEST_SAFE_MODE.name).initialise(
        operator_ack=True, note="pytest session"
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Seed the control plane for whichever database this test ended up pointed at.

    Dozens of test fixtures build a throwaway database of their own, and an
    execution_control table with no row now reads RECOVERY_REQUIRED — the right
    production answer for a database that has never spoken to the broker, and a
    blanket refusal for every order-placing test. This runs after fixture setup,
    so it sees the test's own database, and it only writes when no state exists.
    A test that wants the uninitialised control plane deletes the row in its own
    body, after this has run.
    """
    try:
        from app.services import db

        if db._available and not int(db.get_execution_control().get("initialized", 0)):
            reconciled_control_plane()
    except Exception:
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def _reconciled_control_plane():
    """Give the shared test database the state a completed startup recovery leaves.

    An execution_control table with no row now reads RECOVERY_REQUIRED, which is
    the right production answer — a fresh database has never spoken to the broker
    — and the wrong default for the suite: every order-placing test would refuse
    before reaching what it asserts. Tests about the uninitialised control plane
    build their own database and delete the row themselves.
    """
    try:
        from app.services import db

        db.init()
        db.set_execution_control(
            operator_state="RUNNING",
            recovery_state="CLEAN",
            reason_code="test_session",
            reason="pytest session startup recovery",
            actor="tests",
        )
    except Exception:
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    try:
        test_db_path = os.environ.get("STERLING_DB_PATH")
        if test_db_path and os.path.exists(test_db_path) and "test" in test_db_path:
            os.remove(test_db_path)
    except Exception:
        pass



def reconciled_control_plane():
    """Write the execution-control state a completed startup recovery leaves.

    A database with no execution_control row now reads RECOVERY_REQUIRED, because
    a fresh database has never spoken to the broker. Any test that stands up its
    own database and then places an order has to say that recovery ran.
    """
    from app.services import db

    db.set_execution_control(
        operator_state="RUNNING",
        recovery_state="CLEAN",
        reason_code="test_session",
        reason="pytest startup recovery",
        actor="tests",
    )


def normal_safe_mode_file(monkeypatch, path):
    """Point the safety state at ``path`` and stand it up as NORMAL.

    An absent file is SAFE_MODE, so a test that only wants an isolated safety
    state — rather than an engaged one — has to initialise it, exactly as a real
    machine does on first boot.
    """
    from app.services.safe_mode import SafeModeService

    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    SafeModeService(path).initialise(operator_ack=True, note="test fixture")
    return path


def make_candles(n: int = 100, base: float = 30000.0, trend: float = 10.0) -> List[Candle]:
    np.random.seed(42)
    candles = []
    price = base
    for i in range(n):
        price += trend + np.random.normal(0, base * 0.002)
        o = price - abs(np.random.normal(0, base * 0.001))
        c = price + abs(np.random.normal(0, base * 0.001))
        h = max(o, c) + abs(np.random.normal(0, base * 0.0005))
        l = min(o, c) - abs(np.random.normal(0, base * 0.0005))
        candles.append(
            Candle(
                timestamp_ms=1_700_000_000_000 + i * 3_600_000,
                open=round(o, 2), high=round(h, 2),
                low=round(l, 2), close=round(c, 2),
                volume=float(np.random.uniform(100, 500)),
            )
        )
    return candles


def make_bearish_candles(n: int = 100, base: float = 30000.0) -> List[Candle]:
    return make_candles(n, base, trend=-50.0)


def _default_risk():
    from app.schemas.risk import RiskParams
    from app.core.config import settings
    return RiskParams(
        capital=settings.default_capital,
        max_position_pct=settings.max_position_pct,
        max_contracts=settings.max_contracts,
    )


@pytest.fixture(autouse=True)
def reset_global_stores():
    """Reset every module-level and persisted mutable test store."""
    # `paper_store`, `alert_store`, `exchange_account_store` and the directional
    # engine's caches went with the crypto surface. Importing them here made an
    # AUTOUSE fixture raise, which errored every one of the 3748 collected tests
    # — the suite could not run at all.
    from app.services import eval_history, arrow_store
    from app.services import pnl_history, webhook_store
    from app.services.exchanges.kite import accounts as kite_accounts
    import app.api.v1.endpoints.config as config_ep

    eval_history.clear()
    arrow_store.clear()
    arrow_store._bootstrapped = True
    pnl_history.clear()
    pnl_history._loaded = True
    webhook_store.clear()
    webhook_store._loaded = True
    kite_accounts.clear()
    config_ep._risk = _default_risk()

    yield

    eval_history.clear()
    arrow_store.clear()
    arrow_store._bootstrapped = False
    pnl_history.clear()
    webhook_store.clear()
    kite_accounts.clear()
    config_ep._risk = _default_risk()
