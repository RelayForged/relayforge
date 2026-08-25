from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from .adapter import build_state_update, create_eri_point
from .demo_source import DashboardDemoSource
from .models import StateUpdate


async def _receive_control_messages(
    websocket: WebSocket,
    source: DashboardDemoSource,
) -> None:
    """
    Receive dashboard-owned demo control messages.

    Supported message:

        {
            "type": "demo_control",
            "failure_rate": 0.8,
            "latency_ms": 150.0,
            "failure_mode": "mixed"
        }

    All fields except `type` are optional.
    """
    while True:
        message = await websocket.receive_json()

        if not isinstance(message, dict):
            continue

        if message.get("type") != "demo_control":
            continue

        try:
            failure_rate = message.get("failure_rate")
            latency_ms = message.get("latency_ms")
            failure_mode = message.get("failure_mode")

            source.configure(
                failure_rate=(
                    float(failure_rate)
                    if failure_rate is not None
                    else None
                ),
                latency_ms=(
                    float(latency_ms)
                    if latency_ms is not None
                    else None
                ),
                failure_mode=(
                    str(failure_mode)
                    if failure_mode is not None
                    else None
                ),
            )

        except (TypeError, ValueError):
            # Invalid demo-control messages are ignored.
            # They must never interrupt the live dashboard stream.
            continue


async def send_state_update(websocket: WebSocket) -> None:
    source = DashboardDemoSource()

    history: list[dict[str, object]] = []

    receiver_task = asyncio.create_task(
        _receive_control_messages(websocket, source)
    )

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

    finally:
        receiver_task.cancel()

        with suppress(asyncio.CancelledError):
            await receiver_task