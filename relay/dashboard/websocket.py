from datetime import datetime, timedelta, timezone

from fastapi import WebSocket

from .models import StateUpdate


async def send_state_update(websocket: WebSocket) -> None:
    now = datetime.now(timezone.utc)

    message: StateUpdate = {
        "type": "state_update",
        "state": "AVAILABLE",
        "eri": 100,
        "eri_history": [
            {
                "timestamp": (now - timedelta(minutes=4)).isoformat(),
                "value": 100,
            },
            {
                "timestamp": (now - timedelta(minutes=3)).isoformat(),
                "value": 99,
            },
            {
                "timestamp": (now - timedelta(minutes=2)).isoformat(),
                "value": 100,
            },
            {
                "timestamp": (now - timedelta(minutes=1)).isoformat(),
                "value": 98,
            },
            {
                "timestamp": now.isoformat(),
                "value": 100,
            },
        ],
    }

    await websocket.send_json(message)