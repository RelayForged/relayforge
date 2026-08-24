import asyncio
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from .adapter import build_state_update, create_eri_point
from .demo_source import DashboardDemoSource
from .models import StateUpdate


async def send_state_update(websocket: WebSocket) -> None:
    source = DashboardDemoSource()

    history: list[dict[str, object]] = []

    try:
        while True:
            eri, state, policy, explanation = source.next_evaluation()

            point = create_eri_point(eri)

            history.append(point)
            history = history[-30:]

            message: StateUpdate = build_state_update(
                state=state.value,
                eri=eri,
                eri_history=history,
                policy=policy,
                explanation=explanation,
            )

            await websocket.send_json(message)

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        pass