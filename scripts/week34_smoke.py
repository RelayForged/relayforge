"""
Week 3–4 smoke: Event → Kafka → Dispatcher → Worker → Simulator 5xx
→ Postgres has Delivery, failed attempt, Telemetry, and Retry Manager
attempt 2.

Does not modify the Simulator (Person C). Hits `POST /simulator/config`
and `POST /webhook` on the running Simulator process.

    .venv/bin/python scripts/week34_smoke.py

Requires: docker compose up, `alembic upgrade head`, Simulator on
SIMULATOR_URL (default http://localhost:8000).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from typing import Optional

import httpx

from relay.data_plane.dispatcher.main import Dispatcher
from relay.infrastructure.messaging.consumer import (
    EVENTS_BUSINESS_TOPIC,
    EVENTS_INTERNAL_DELIVERY_TOPIC,
    KafkaConsumer,
)
from relay.infrastructure.messaging.producer import EVENTS_BUSINESS_TOPIC as PUBLISH_TOPIC
from relay.infrastructure.messaging.producer import close, publish
from relay.infrastructure.persistence.delivery_attempt_repository import (
    DeliveryAttemptRepository,
)
from relay.infrastructure.persistence.delivery_repository import DeliveryRepository
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.infrastructure.persistence.event_repository import EventRepository
from relay.infrastructure.persistence.subscription_repository import (
    SubscriptionRepository,
)
from relay.infrastructure.persistence.telemetry_repository import TelemetryRepository
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.value_objects.identifiers import CausationID, CorrelationID
from relay.kernel.value_objects.telemetry import Telemetry

SIMULATOR_URL = os.environ.get("SIMULATOR_URL", "http://localhost:8000")
WEBHOOK_URL = f"{SIMULATOR_URL.rstrip('/')}/webhook"
POLL_SECONDS = 30.0
EVENT_TYPE = f"Week34Smoke-{uuid.uuid4()}"


def _ok(flag: bool) -> str:
    return "OK" if flag else "FAIL"


async def _configure_simulator(client: httpx.AsyncClient) -> Optional[subprocess.Popen[bytes]]:
    url = f"{SIMULATOR_URL.rstrip('/')}/simulator/config"
    payload = {"mode": "http_error", "rate": 1.0}
    try:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return None
    except httpx.ConnectError:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "simulator.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return proc
            except (httpx.ConnectError, httpx.HTTPError):
                await asyncio.sleep(0.1)
        proc.terminate()
        raise RuntimeError(
            f"Simulator not reachable at {url}. "
            "Start it with: .venv/bin/uvicorn simulator.main:app --host 0.0.0.0 --port 8000"
        )


async def _wait_for_pipeline(
    event_id: str,
    subscription_id: str,
    deliveries: DeliveryRepository,
    attempts: DeliveryAttemptRepository,
    telemetry: TelemetryRepository,
) -> tuple[Delivery, list[DeliveryAttempt], Optional[Telemetry]]:
    deadline = time.monotonic() + POLL_SECONDS
    last: tuple[Optional[Delivery], list[DeliveryAttempt], Optional[Telemetry]] = (
        None,
        [],
        None,
    )
    while time.monotonic() < deadline:
        delivery = await deliveries.get_by_event_and_subscription(
            event_id, subscription_id
        )
        rows: list[DeliveryAttempt] = []
        observation: Optional[Telemetry] = None
        if delivery is not None:
            rows = await attempts.list_by_delivery_id(delivery.delivery_id)
            first = next((row for row in rows if row.attempt_number == 1), None)
            if first is not None:
                observation = await telemetry.get_by_id(first.attempt_id)
            last = (delivery, rows, observation)
            failed = (
                first is not None
                and first.success is False
                and first.completed_at is not None
            )
            tel_ok = (
                observation is not None
                and observation.succeeded is False
                and observation.attempt_id == first.attempt_id
            )
            scheduled = any(row.attempt_number == 2 for row in rows)
            if failed and tel_ok and scheduled:
                return delivery, rows, observation
        await asyncio.sleep(0.25)
    raise TimeoutError(
        f"Postgres did not show Delivery + failed attempt + Telemetry + attempt 2 "
        f"within {POLL_SECONDS}s (last={last!r})"
    )


def _print_matrix(
    delivery: Delivery,
    attempts: list[DeliveryAttempt],
    observation: Optional[Telemetry],
) -> None:
    first = next((row for row in attempts if row.attempt_number == 1), None)
    second = next((row for row in attempts if row.attempt_number == 2), None)
    failed = (
        first is not None
        and first.success is False
        and first.completed_at is not None
    )
    tel_ok = observation is not None and observation.succeeded is False
    print("Week 3-4 smoke matrix")
    print(
        f"  [{_ok(True)}] Delivery              "
        f"id={delivery.delivery_id} status={delivery.status}"
    )
    print(
        f"  [{_ok(failed)}] Attempt 1 failed      "
        f"id={getattr(first, 'attempt_id', None)} "
        f"http_status={getattr(first, 'http_status', None)} "
        f"success={getattr(first, 'success', None)}"
    )
    print(
        f"  [{_ok(tel_ok)}] Telemetry             "
        f"attempt_id={getattr(observation, 'attempt_id', None)} "
        f"succeeded={getattr(observation, 'succeeded', None)} "
        f"http_status={getattr(observation, 'http_status', None)} "
        f"latency_ms={getattr(observation, 'latency_ms', None)}"
    )
    print(
        f"  [{_ok(second is not None)}] Attempt 2 scheduled   "
        f"id={getattr(second, 'attempt_id', None)} "
        f"attempt_number={getattr(second, 'attempt_number', None)} "
        f"started_at={getattr(second, 'started_at', None)}"
    )


async def main() -> None:
    sim_proc: Optional[subprocess.Popen[bytes]] = None
    dispatcher_task: Optional[asyncio.Task[None]] = None
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            sim_proc = await _configure_simulator(client)

            endpoint = Endpoint(
                target_url=WEBHOOK_URL,
                owner_id="week34-smoke",
            )
            await EndpointRepository().save(endpoint)

            subscription = Subscription(
                endpoint_id=endpoint.endpoint_id,
                event_types=[EVENT_TYPE],
                status="ACTIVE",
            )
            await SubscriptionRepository().save(subscription)

            group_id = f"relay-dispatcher-smoke-{uuid.uuid4()}"
            dispatcher = Dispatcher(
                consumer=KafkaConsumer(
                    topics=(EVENTS_BUSINESS_TOPIC, EVENTS_INTERNAL_DELIVERY_TOPIC),
                    group_id=group_id,
                )
            )
            dispatcher_task = asyncio.create_task(dispatcher.run())
            await asyncio.sleep(1.0)

            correlation_id = CorrelationID()
            event = Event(
                correlation_id=correlation_id,
                causation_id=CausationID(value=correlation_id.value),
                event_type=EVENT_TYPE,
                payload={"source": "week34_smoke"},
            )
            await EventRepository().save(event)
            await publish(
                PUBLISH_TOPIC,
                key=event.event_id,
                value=event.model_dump_json().encode("utf-8"),
            )

            delivery, attempts, observation = await _wait_for_pipeline(
                event.event_id,
                subscription.subscription_id,
                DeliveryRepository(),
                DeliveryAttemptRepository(),
                TelemetryRepository(),
            )
            _print_matrix(delivery, attempts, observation)
            print(f"event_id={event.event_id}")
            print("Week 3-4 smoke OK")
        finally:
            if dispatcher_task is not None:
                dispatcher_task.cancel()
                try:
                    await dispatcher_task
                except asyncio.CancelledError:
                    pass
            await close()
            if sim_proc is not None:
                sim_proc.terminate()
                sim_proc.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
