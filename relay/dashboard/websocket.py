import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from .adapter import build_state_update
from .models import StateUpdate


async def send_state_update(websocket: WebSocket) -> None:
    eri = 100
    history: list[dict[str, object]] = []

    try:
        while True:
            point = {
                "timestamp": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                "value": eri,
            }

            history.append(point)
            history = history[-30:]

            message: StateUpdate = build_state_update(
                state="AVAILABLE",
                eri=eri,
                eri_history=history,
            )

            await websocket.send_json(message)

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        pass