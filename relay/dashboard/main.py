from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Optional
from uuid import uuid4

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from relay.api.chart import chart_router
from relay.api.chart.stores import state_store
from relay.control_plane.explainability.record import ExplainabilityRecord
from relay.infrastructure.cors import cors_origins
from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_POLICY_TOPIC
from relay.infrastructure.messaging.consumer import KafkaConsumer
from relay.kernel.entities.policy_evaluation import PolicyEvaluation
from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore
from .websocket import send_state_update

logger = logging.getLogger(__name__)

DASHBOARD_GROUP_ID = "relay-dashboard"


def _consumer_group_id() -> str:
    """
    A consumer group of its own for each dashboard process.

    The dashboard's state is a read model built from the policy topic, held
    in memory. With a shared group, a restarted dashboard resumed after the
    last committed offset and came back with empty charts. A new group
    starts from the earliest offset, so every start rebuilds the history
    from the log, which stays the source of truth.
    """
    return f"{DASHBOARD_GROUP_ID}-{uuid4().hex[:12]}"


async def _consume_policy_events() -> None:
    """Consume policy evaluation events from Kafka and record into state_store."""
    consumer = KafkaConsumer(
        topics=[EVENTS_INTERNAL_POLICY_TOPIC],
        group_id=_consumer_group_id(),
    )
    try:
        await consumer.start()
    except Exception as exc:
        logger.warning(
            "Failed to start Kafka consumer in dashboard (%s); live policy streaming disabled",
            exc,
        )
        return

    try:
        async for msg in consumer:
            try:
                data = json.loads(msg.value.decode("utf-8"))
                if data.get("type") == "policy_tier":
                    # The Dispatcher announcing a fallback tier (ADR 0024).
                    state_store.record_policy_tier(data["endpoint_id"], int(data["tier"]))
                    await consumer.commit(msg)
                    continue
                endpoint_id = data["endpoint_id"]
                state = EndpointState(data["state"])
                eri_score = ERIScore.model_validate(data["eri_score"])
                policy_evaluation = PolicyEvaluation.model_validate(data["policy_evaluation"])
                delivery_policy = DeliveryPolicy.model_validate(data["delivery_policy"])
                explainability_record = (
                    ExplainabilityRecord.model_validate(data["explainability_record"])
                    if data.get("explainability_record")
                    else None
                )
                state_store.record_evaluation(
                    endpoint_id=endpoint_id,
                    state=state,
                    eri_score=eri_score,
                    policy_evaluation=policy_evaluation,
                    delivery_policy=delivery_policy,
                    explainability_record=explainability_record,
                )
                await consumer.commit(msg)
            except Exception as exc:
                logger.warning("Failed to process policy evaluation message: %s", exc)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Error in dashboard policy consumer loop: %s", exc)
    finally:
        await consumer.close()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_consume_policy_events())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="RelayForge Dashboard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(chart_router)


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, endpoint_id: Optional[str] = None
) -> None:
    await websocket.accept()
    await send_state_update(websocket, endpoint_id)


# The built React UI (Dockerfile `frontend` stage), so the demo is one URL.
# Mounted last so the chart API and /ws keep their routes. Absent until the
# UI has been built; under `npm run dev` Vite serves it instead.
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")