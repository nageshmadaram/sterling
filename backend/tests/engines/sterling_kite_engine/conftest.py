import numpy as np
import pytest

from tests.conftest import reset_global_stores as _shared_reset_global_stores


@pytest.fixture
def isolated_kite_database(tmp_path):
    """Exercise real persistence without reloading another test's positions.

    The global store fixture also writes SQLite, so this fixture must wrap its
    setup and teardown. Restoring the DB path only after cleanup keeps those
    writes away from any database configured by the caller.
    """
    from app.services import db, live_safety
    from app.services.kite_engine import monitor, positions, service, state

    def clear_caches():
        positions.reset()
        state.reset()
        live_safety._IDEMPOTENCY_CACHE.clear()
        monitor._exiting.clear()
        monitor._stop_probe.clear()
        monitor.forget_holdings()
        service._entry_locks.clear()
        service._red_stale_warned.clear()
        service._orphan_warned.clear()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "_DB_PATH", str(tmp_path / "kite-test.sqlite3"))
        patch.setattr(db, "_available", False)
        assert db.init(), "Kite tests require an isolated, working SQLite database"
        clear_caches()
        try:
            yield
        finally:
            clear_caches()


@pytest.fixture(autouse=True)
def reset_global_stores(isolated_kite_database):
    """Retain shared cleanup, with an explicit dependency on DB isolation."""
    yield from _shared_reset_global_stores.__wrapped__()


def series(values):
    """Build OHLC arrays from a close path; tight bars so HA tracks closely."""
    c = np.asarray(values, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + 1.0
    l = np.minimum(o, c) - 1.0
    return o, h, l, c


@pytest.fixture
def uptrend():
    # long, smooth rise — drives all three SuperTrends bullish after warmup
    return series(list(np.linspace(100, 400, 120)))


@pytest.fixture
def down_then_up():
    # falling then rising — produces a bear→bull transition
    fall = list(np.linspace(300, 150, 60))
    rise = list(np.linspace(150, 450, 60))
    return series(fall + rise)
