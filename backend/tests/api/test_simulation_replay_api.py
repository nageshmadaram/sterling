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
    # The walk skips weekends but not exchange holidays, and says so rather than
    # letting the client assume every listed date is a session.
    assert body["holidays_filtered"] is False


def test_available_dates_never_lists_a_weekend():
    from datetime import datetime

    body = _client().get("/api/v1/simulation/available-dates").json()
    for iso in body["dates"]:
        assert datetime.strptime(iso, "%Y-%m-%d").weekday() < 5
