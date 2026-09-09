"""Shared WebSocket fan-out for Zerodha/Kite account and tick channels."""
import json
import logging
from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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
async def websocket_endpoint(websocket: WebSocket):
    await stream_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                action = payload.get("action")
                channel = payload.get("channel")
                
                if action == "subscribe" and channel:
                    stream_manager.subscribe(websocket, channel)
                if isinstance(channel, str):
                    channel = channel.strip()
                    # Sanitize: allow only safe channel identifier tokens
                    if channel and all(c.isalnum() or c in ":_-" for c in channel):
                        if action == "subscribe":
                            stream_manager.subscribe(websocket, channel)
                        elif action == "unsubscribe":
                            stream_manager.unsubscribe(websocket, channel)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        stream_manager.disconnect(websocket)
