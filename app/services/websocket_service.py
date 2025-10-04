from typing import Dict

from fastapi import WebSocket
from fastapi.websockets import WebSocketState


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]

    async def send_personal_message(self, message: str, client_id: str):
        websocket = self.active_connections.get(client_id)
        if websocket and websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(message)

    async def broadcast(self, message: str):
        disconnected_clients = []
        for client_id, websocket in self.active_connections.items():
            try:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(message)
                else:
                    disconnected_clients.append(client_id)
            except:  # noqa
                disconnected_clients.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected_clients:
            self.disconnect(client_id)
