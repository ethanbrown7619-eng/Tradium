"""
WebSocket manager for real-time dashboard updates.
Broadcasts scanner results, trade executions, and status changes to connected clients.
"""
import json
import logging
from typing import Dict, Set
from uuid import UUID
from fastapi import WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from app.config import get_settings

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections per user."""

    def __init__(self):
        # user_id -> set of active websocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)
        logger.info(f"WebSocket connected for user {user_id}")

    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"WebSocket disconnected for user {user_id}")

    async def send_to_user(self, user_id: str, message: dict):
        """Send message to all connections of a specific user."""
        if user_id not in self.active_connections:
            return
        dead = []
        for ws in self.active_connections[user_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active_connections[user_id].discard(ws)

    async def broadcast(self, message: dict):
        """Broadcast to all connected users."""
        for user_id in list(self.active_connections.keys()):
            await self.send_to_user(user_id, message)

    @property
    def connected_count(self) -> int:
        return sum(len(conns) for conns in self.active_connections.values())


manager = ConnectionManager()


async def authenticate_ws(websocket: WebSocket) -> str:
    """Extract and validate user from WebSocket query params."""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        raise WebSocketDisconnect(code=4001)

    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload["sub"]
    except (JWTError, KeyError):
        await websocket.close(code=4001, reason="Invalid token")
        raise WebSocketDisconnect(code=4001)


async def ws_endpoint(websocket: WebSocket):
    """Main WebSocket endpoint handler."""
    user_id = await authenticate_ws(websocket)
    await manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle client messages (e.g., ping, subscribe to specific feeds)
            try:
                msg = json.loads(data)
                msg_type = msg.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "subscribe":
                    # Future: channel-based subscriptions
                    await websocket.send_json({"type": "subscribed", "channel": msg.get("channel")})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
