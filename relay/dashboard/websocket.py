
from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from typing import Any, Awaitable, Callable, Optional

from fastapi import WebSocket, WebSocketDisconnect

from relay.api.chart.stores import state_store
from relay.kernel.value_objects.eri import ERIWeights

from .adapter import (
    build_live_state_update,
    build_state_update,
    create_eri_point,
    get_delivery_event,
)
from .demo_source import DashboardDemoSource
from .models import StateUpdate

logger = logging.getLogger(__name__)

SYNTHETIC_SOURCE_ENV = "DASHBOARD_SYNTHETIC_SOURCE"
LIVE_INTERVAL_SECONDS = 1.0
SYNTHETIC_INTERVAL_SECONDS = 2.0
DELIVERY_EVENT_LIMIT = 30

DeliveryEventFetcher = Callable[[str], Awaitable[list[dict[str, Any]]]]


def synthetic_source_enabled() -> bool:
    """
    The synthetic source is opt-in only. A dashboard that quietly fell
    back to generated telemetry would present invented data as real.
    """
    return os.environ.get(SYNTHETIC_SOURCE_ENV, "false").lower() in (
        "true",
        "1",
        "yes",
    )


async def fetch_delivery_events(endpoint_id: str) -> list[dict[str, Any]]:
    """Recent completed attempts for the timeline, newest first. Read-only."""
    from relay.infrastructure.persistence.delivery_attempt_repository import (
        DeliveryAttemptRepository,
    )

    rows = await DeliveryAttemptRepository().list_recent_by_endpoint(
        endpoint_id, limit=DELIVERY_EVENT_LIMIT
    )
    return [
        get_delivery_event(delivery, attempt)
        for delivery, attempt in rows
    ]


async def _receive_control_messages(
    websocket: WebSocket,
    source: Optional[DashboardDemoSource],
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

    On the live stream, messages are read and dropped so the receive
    loop still notices a disconnect.
    """
    while True:
        message = await websocket.receive_json()

        if source is None:
            continue

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
            continue


async def _stream_synthetic(
    websocket: WebSocket,
    source: DashboardDemoSource,
) -> None:
    history: list[dict[str, object]] = []

    while True:
        eri, state, policy, explanation, sub_scores = (
            source.next_evaluation()
        )

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

        # Include the sub-scores and weights used by the radar chart.
        message["eri_sub_scores"] = sub_scores
        message["eri_weights"] = {
            "success_rate": source.weights.success_rate_weight,
            "latency": source.weights.latency_weight,
            "consecutive_failures": (
                source.weights.consecutive_failures_weight
            ),
            "timeout_rate": source.weights.timeout_weight,
            "http_5xx_rate": source.weights.http_5xx_weight,
            "queue_depth": source.weights.queue_depth_weight,
            "recovery_trend": source.weights.recovery_trend_weight,
        }

        message["source"] = "synthetic"
        message["endpoint_id"] = source.endpoint_id
        message["thresholds"] = (
            source.strategy.thresholds.model_dump()
        )

        await websocket.send_json(message)
        await asyncio.sleep(SYNTHETIC_INTERVAL_SECONDS)


async def _stream_live(
    websocket: WebSocket,
    endpoint_id: Optional[str],
    fetch_events: DeliveryEventFetcher,
    interval_seconds: float,
) -> None:
    # The policy event does not carry the weights, so use the platform default.
    weights = ERIWeights.optimal()
    events_failed = False

    while True:
        # Without an explicit endpoint, follow whichever was evaluated last.
        current = endpoint_id or state_store.get_latest_endpoint_id()

        events: list[dict[str, Any]] = []
        if current is not None:
            try:
                events = await fetch_events(current)
                events_failed = False
            except Exception:
                if not events_failed:
                    logger.exception(
                        "Dashboard delivery-event query failed endpoint_id=%s",
                        current,
                    )
                events_failed = True

        message: StateUpdate = build_live_state_update(
            endpoint_id=current,
            store=state_store,
            weights=weights,
            delivery_events=events,
        )

        await websocket.send_json(message)
        await asyncio.sleep(interval_seconds)


async def send_state_update(
    websocket: WebSocket,
    endpoint_id: Optional[str] = None,
    *,
    fetch_events: DeliveryEventFetcher = fetch_delivery_events,
    interval_seconds: float = LIVE_INTERVAL_SECONDS,
) -> None:
    source = (
        DashboardDemoSource()
        if synthetic_source_enabled()
        else None
    )

    if source is not None:
        stream = _stream_synthetic(websocket, source)
    else:
        stream = _stream_live(
            websocket,
            endpoint_id,
            fetch_events,
            interval_seconds,
        )

    receiver_task = asyncio.create_task(
        _receive_control_messages(websocket, source)
    )
    stream_task = asyncio.create_task(stream)

    try:
        # Whichever side ends first ends the connection.
        done, _ = await asyncio.wait(
            {receiver_task, stream_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(
                exc, WebSocketDisconnect
            ):
                raise exc

    finally:
        for task in (receiver_task, stream_task):
            task.cancel()

            with suppress(
                asyncio.CancelledError,
                WebSocketDisconnect,
            ):
                await task
