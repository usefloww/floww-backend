import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.deps.auth import CurrentUser
from app.services.websocket_service import ConnectionManager

router = APIRouter()


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user: CurrentUser):
    client_id = user.workos_user_id
    await manager.connect(websocket)

    print(f"Client {client_id} connected")

    # Send welcome message
    await manager.send_personal_message(
        json.dumps(
            {
                "type": "welcome",
                "message": f"Connected with client ID: {client_id}",
                "timestamp": str(asyncio.get_event_loop().time()),
            }
        ),
        client_id,
    )

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            # Handle different message types
            if message.get("type") == "ping":
                # Ping-pong functionality
                await manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "pong",
                            "message": "pong",
                            "original_data": message.get("data"),
                            "timestamp": str(asyncio.get_event_loop().time()),
                        }
                    ),
                    client_id,
                )
                print(f"Ping-pong with client {client_id}")

            elif message.get("type") == "echo":
                # Echo functionality
                await manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "echo",
                            "message": message.get("message", ""),
                            "timestamp": str(asyncio.get_event_loop().time()),
                        }
                    ),
                    client_id,
                )
                print(f"Echo to client {client_id}: {message.get('message')}")

            elif message.get("type") == "broadcast":
                # Broadcast to all connected clients
                broadcast_msg = json.dumps(
                    {
                        "type": "broadcast",
                        "from": client_id,
                        "message": message.get("message", ""),
                        "timestamp": str(asyncio.get_event_loop().time()),
                    }
                )
                await manager.broadcast(broadcast_msg)
                print(f"Broadcast from client {client_id}: {message.get('message')}")

            else:
                # Unknown message type
                await manager.send_personal_message(
                    json.dumps(
                        {
                            "type": "error",
                            "message": f"Unknown message type: {message.get('type')}",
                            "timestamp": str(asyncio.get_event_loop().time()),
                        }
                    ),
                    client_id,
                )

    except WebSocketDisconnect:
        manager.disconnect(client_id)
        print(f"Client {client_id} disconnected")

        # Notify other clients about disconnection
        await manager.broadcast(
            json.dumps(
                {
                    "type": "user_left",
                    "client_id": client_id,
                    "timestamp": str(asyncio.get_event_loop().time()),
                }
            )
        )
