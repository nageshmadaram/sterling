import pytest
from starlette.testclient import TestClient

from app.core import tokens
from main import app


def test_ws_default_user_channel_auth():
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        # Default user subscribing to kite_orders:default succeeds
        ws.send_json({"action": "subscribe", "channel": "kite_orders:default"})

        # Default user attempting to subscribe to kite_orders:alice_uid is rejected
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        resp = ws.receive_json()
        assert resp.get("type") == "error"
        assert resp.get("error") == "unauthorized_channel"


def test_ws_authenticated_jwt_channel_auth():
    client = TestClient(app)
    token = tokens.mint_access("alice_uid", "stream_alice", "trader", 1)

    with client.websocket_connect(f"/api/v1/stream/ws?token={token}") as ws:
        # Authorized user subscribing to own orders succeeds
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})

        # Authorized user attempting to subscribe to another user's orders is rejected
        ws.send_json({"action": "subscribe", "channel": "kite_orders:bob_uid"})
        resp = ws.receive_json()
        assert resp.get("type") == "error"
        assert resp.get("error") == "unauthorized_channel"


def test_ws_runtime_auth_action():
    client = TestClient(app)
    token = tokens.mint_access("alice_uid", "stream_alice", "trader", 1)

    with client.websocket_connect("/api/v1/stream/ws") as ws:
        # Send runtime auth action
        ws.send_json({"action": "auth", "token": token})
        resp = ws.receive_json()
        assert resp.get("type") == "auth_ok"
        assert resp.get("user_id") == "alice_uid"

        # Now subscribing to own channel works without error
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})

        # But subscribing to another user fails
        ws.send_json({"action": "subscribe", "channel": "kite_orders:charlie_uid"})
        err = ws.receive_json()
        assert err.get("type") == "error"
        assert err.get("error") == "unauthorized_channel"


def test_ws_public_channel_allowed():
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        # Public non-user channels are allowed for everyone
        ws.send_json({"action": "subscribe", "channel": "market_depth:INFY"})

