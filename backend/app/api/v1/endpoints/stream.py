"""Shared WebSocket fan-out for Zerodha/Kite account and tick channels."""
import json
import logging
from typing import Dict, List
from typing import Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import tokens
from app.core.auth import DEFAULT_USER_ID, USER_ID_HEADER

log = logging.getLogger(__name__)
router = APIRouter()

class StreamManager:
    def __init__(self):
        # Map channel names (e.g., symbols) to a list of active websocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}
        # Keep track of which connections are open to avoid exceptions on disconnected sockets
        self.all_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.all_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.all_connections:
            self.all_connections.remove(websocket)
        for channel in list(self.active_connections.keys()):
            if websocket in self.active_connections[channel]:
                self.active_connections[channel].remove(websocket)
                if not self.active_connections[channel]:
                    del self.active_connections[channel]

    def subscribe(self, websocket: WebSocket, channel: str):
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        if websocket not in self.active_connections[channel]:
            self.active_connections[channel].append(websocket)

    def unsubscribe(self, websocket: WebSocket, channel: str):
        if channel in self.active_connections and websocket in self.active_connections[channel]:
            self.active_connections[channel].remove(websocket)
            if not self.active_connections[channel]:
                del self.active_connections[channel]

    async def broadcast_to_channel(self, channel: str, message: dict):
        if channel in self.active_connections:
            dead_connections = []
            for connection in self.active_connections[channel]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    log.warning(f"Failed to send to websocket, removing: {e}")
                    dead_connections.append(connection)
            
            for dead in dead_connections:
                self.disconnect(dead)

stream_manager = StreamManager()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    await stream_manager.connect(websocket)
    conn_uid = websocket.headers.get(USER_ID_HEADER) or DEFAULT_USER_ID
    auth_token = token or websocket.query_params.get("token")
    if not auth_token:
        auth_hdr = websocket.headers.get("authorization") or ""
        if auth_hdr.lower().startswith("bearer "):
            auth_token = auth_hdr[7:].strip()
    if auth_token:
        try:
            tok_payload = tokens.decode(auth_token, expected_typ="access")
            conn_uid = str(tok_payload.get("sub") or conn_uid)
        except Exception as exc:
            log.warning("WebSocket token verification failed: %s", exc)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                action = payload.get("action")

                if action == "auth":
                    msg_token = payload.get("token")
                    if msg_token:
                        try:
                            tok_payload = tokens.decode(str(msg_token), expected_typ="access")
                            conn_uid = str(tok_payload.get("sub") or conn_uid)
                            await websocket.send_json({"type": "auth_ok", "user_id": conn_uid})
                        except Exception as exc:
                            await websocket.send_json({"type": "auth_error", "detail": str(exc)})
                    continue

                channel = payload.get("channel")
                if isinstance(channel, str):
                    channel = channel.strip()
                    # Sanitize: allow only safe channel identifier tokens
                    if channel and all(c.isalnum() or c in ":_-" for c in channel):
                        if action == "subscribe":
                            # Enforce user authorization on user-scoped channels
                            if channel.startswith(("kite_orders:", "orders:", "positions:", "kite_ticks:")):
                                parts = channel.split(":", 1)
                                target_uid = parts[1] if len(parts) > 1 else ""
                                if target_uid and target_uid != conn_uid:
                                    log.warning(
                                        "Blocked unauthorized subscription: connection user '%s' tried subscribing to '%s'",
                                        conn_uid, channel,
                                    )
                                    await websocket.send_json({
                                        "type": "error",
                                        "error": "unauthorized_channel",
                                        "channel": channel,
                                        "detail": f"Access denied to channel for user '{target_uid}'",
                                    })
                                    continue
                            stream_manager.subscribe(websocket, channel)
                        elif action == "unsubscribe":
                            stream_manager.unsubscribe(websocket, channel)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        stream_manager.disconnect(websocket)
