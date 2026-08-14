import asyncio
import random
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from .models import ERIPoint, StateUpdate


async def send_state_update(websocket: WebSocket) -> None:
    eri = 100

    history: list[ERIPoint] = []

    try:
        while True:
            change = random.choice([-2, -1, 0, 0, 0, 1, 2])
            eri = max(0, min(100, eri + change))

            now = datetime.now(timezone.utc)

            history.append(
                {
                    "timestamp": now.isoformat(),
                    "value": eri,
                }
            )

            history = history[-30:]

            message: StateUpdate = {
                "type": "state_update",
                "state": "AVAILABLE",
                "eri": eri,
                "eri_history": history,
            }

            await websocket.send_json(message)

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        pass