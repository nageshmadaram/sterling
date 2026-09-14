"""The replay HTTP surface.

Covers the three things the client cannot work around on its own: whether a
duplicate start is distinguishable from a running replay, whether it can poll
deltas instead of the whole ledger, and whether `/available-dates` admits when
its dates are synthetic.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.simulation import router
from app.services.simulation import SimState, SimStats, simulation_runner


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _idle_runner():
    simulation_runner._state = SimState.IDLE
    simulation_runner._stats = SimStats()
    yield
    simulation_runner._state = SimState.IDLE
    simulation_runner._stats = SimStats()


def test_status_publishes_capabilities():
    body = _client().get("/api/v1/simulation/status").json()
    assert body["capabilities"]["friction"] is True
    assert body["capabilities"]["multi_day"] is True
    assert "5m" in body["capabilities"]["resolutions"]
    assert "run_id" in body
    assert "revision" in body


def test_start_over_a_running_replay_is_a_409_not_a_silent_restart():
    simulation_runner._state = SimState.RUNNING
    res = _client().post("/api/v1/simulation/start", json={"date": "2026-09-04"})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "already_running"


def test_pause_while_idle_returns_a_machine_readable_code():
    res = _client().post("/api/v1/simulation/pause")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "not_running"


def test_resume_while_idle_returns_a_machine_readable_code():
    res = _client().post("/api/v1/simulation/resume")
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "not_paused"


def test_seek_while_idle_is_refused():
    res = _client().post("/api/v1/simulation/seek", json={"to_pct": 50})
    assert res.status_code == 400


def test_status_delta_query_returns_only_unseen_rows():
    from app.services.simulation import SimSignalEvent

    simulation_runner._stats = SimStats(events=[
        SimSignalEvent(
            time_iso="09:2%d:00" % i, timestamp_ms=i, strategy="supertrend",
            instrument="NIFTY", direction="BULLISH", strength="STRONG",
            entry=100.0, stop=90.0, target=120.0,
        )
        for i in range(5)
    ])
    body = _client().get("/api/v1/simulation/status?since_events=3").json()
    assert len(body["stats"]["events"]) == 2
    assert body["events_total"] == 5


def test_status_without_offsets_is_unchanged():
    """Existing clients must keep receiving the whole payload."""
    from app.services.simulation import SimSignalEvent

    simulation_runner._stats = SimStats(events=[
        SimSignalEvent(
            time_iso="09:20:00", timestamp_ms=1, strategy="supertrend",
            instrument="NIFTY", direction="BULLISH", strength="STRONG",
            entry=100.0, stop=90.0, target=120.0,
        )
    ])
    body = _client().get("/api/v1/simulation/status").json()
    assert len(body["stats"]["events"]) == 1


def test_available_dates_declares_whether_its_dates_are_real():
    body = _client().get("/api/v1/simulation/available-dates?instrument=NIFTY").json()
    assert body["source"] in ("store", "fallback")
    assert body["instrument"] == "NIFTY"
    assert body["resolution"] == "5m"
    assert body["holidays_filtered"] is True


def test_available_dates_never_lists_a_weekend():
    from datetime import datetime

    body = _client().get("/api/v1/simulation/available-dates").json()
    for iso in body["dates"]:
        assert datetime.strptime(iso, "%Y-%m-%d").weekday() < 5


def test_available_dates_are_actual_rows_not_every_date_between_bounds(monkeypatch):
    from datetime import datetime
    from app.engines.snapback.models import IST
    from app.api.v1.endpoints import simulation as endpoint

    start = int(datetime(2026, 9, 11, 9, 15, tzinfo=IST).timestamp())
    end = int(datetime(2026, 9, 15, 15, 30, tzinfo=IST).timestamp())
    monkeypatch.setattr(
        "app.services.ohlcv_store.get_symbol_coverage",
        lambda *_a, **_k: {"earliest": start, "latest": end, "count": 2},
    )
    monkeypatch.setattr(
        "app.services.ohlcv_store.get_session_dates",
        lambda *_a, **_k: ["2026-09-11", "2026-09-15"],
    )

    body = endpoint._available_dates_sync("NIFTY", "5m")

    assert body.source == "store"
    assert body.dates == ["2026-09-11", "2026-09-15"]
    assert "2026-09-14" not in body.dates


def test_hydration_failure_ends_in_error_state(monkeypatch):
    from app.services import simulation as sim

    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(sim, "_hydrate_missing_candles", fail)
    runner = sim.SimulationRunner()
    runner._config = sim.SimConfig(date="2026-09-04", instruments=["NIFTY"], strategies=["snapback"])
    runner._state = sim.SimState.LOADING

    import asyncio
    asyncio.run(runner._run_loop())

    assert runner.status.state == sim.SimState.ERROR
    assert runner.status.session_complete is False
    assert "provider down" in runner.status.status_message
