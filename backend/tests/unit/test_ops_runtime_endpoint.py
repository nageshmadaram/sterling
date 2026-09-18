"""The loopback runtime view: what it answers, and who may ask.

This endpoint is unauthenticated on purpose — a start script should not need a
credential to ask whether the service came up — which is exactly why it must
refuse anyone who is not on the loopback interface.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.snapback_ops import router


def _app_with(api_router):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture()
def client():
    # TestClient reports "testclient" as the peer unless told otherwise, which
    # this endpoint correctly refuses — so a loopback peer is stated explicitly.
    return TestClient(_app_with(router), client=("127.0.0.1", 51234))


def test_loopback_may_read_the_runtime_view(client):
    response = client.get("/api/v1/ops/runtime")
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"broker", "feed", "checked_at"}


def test_a_remote_caller_is_not_told_the_endpoint_exists():
    # 404 rather than 403: an unauthenticated operational endpoint should not
    # confirm its own existence to the network.
    remote = TestClient(_app_with(router), client=("10.1.2.3", 51234))
    assert remote.get("/api/v1/ops/runtime").status_code == 404


def test_every_field_is_tri_state_and_never_guesses(client, monkeypatch):
    """An unreadable source must answer null, never a reassuring default."""
    def _explode(*_a, **_k):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("app.services.account_binding_service.active_binding", _explode)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.all_accounts", _explode)
    monkeypatch.setattr("app.services.exchanges.kite.ticker_manager.known_users", _explode)

    body = client.get("/api/v1/ops/runtime").json()
    assert body["broker"]["connected"] is None
    assert body["broker"]["binding_matches"] is None
    assert body["feed"]["connected"] is None
    assert "unreadable" in body["broker"]["detail"]
