"""
Demo scenario engine: one call configures the Endpoint Simulator and keeps
events flowing, so a live demo needs no terminal.

This is demo tooling, not platform behavior. It makes no delivery
decisions: it sets the simulator's fault mode and publishes ordinary
business events through the same persist-then-publish path as
`POST /events`. Everything the audience then sees (ERI, state, policy) is
produced by the real pipeline reacting to those events.

The router is mounted only when RELAY_DEMO_ENABLED is true (see
relay/api/main.py). The publisher task is the one piece of process-local
state; `GET /demo/status` reports it and the app lifespan stops it.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from relay.api.deps import (
    get_endpoint_repository,
    get_event_repository,
    get_subscription_repository,
)
from relay.api.endpoints import publish_restore
from relay.api.events import build_event, persist_and_publish
from relay.api.security import require_owner
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.infrastructure.persistence.event_repository import EventRepository
from relay.infrastructure.persistence.subscription_repository import (
    SubscriptionRepository,
)
from relay.infrastructure.url_guard import UnsafeTargetURL, check_url
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.subscription import Subscription

logger = logging.getLogger(__name__)

DEMO_OWNER_ID = "relay-demo"
DEMO_EVENT_TYPE = "demo.order.created"
DEFAULT_RATE_PER_SEC = 2.0
SIMULATOR_TIMEOUT_SECONDS = 5.0
# Must exceed the worker's HTTP timeout (RELAY_WEBHOOK_TIMEOUT_SECONDS,
# default 5 s): the simulator's timeout mode only delays its 200, so a
# shorter delay would be recorded as a slow success, not a timeout.
TIMEOUT_LATENCY_MS = 6000


def demo_enabled() -> bool:
    return os.environ.get("RELAY_DEMO_ENABLED", "false").lower() in ("true", "1", "yes")


def _simulator_url() -> str:
    return os.environ.get("SIMULATOR_URL", "http://localhost:8088").rstrip("/")


def _target_url() -> str:
    return os.environ.get("DEMO_TARGET_URL", f"{_simulator_url()}/webhook")


def _rate_per_sec() -> float:
    try:
        rate = float(os.environ.get("DEMO_EVENTS_PER_SEC", str(DEFAULT_RATE_PER_SEC)))
    except ValueError:
        return DEFAULT_RATE_PER_SEC
    return rate if rate > 0 else DEFAULT_RATE_PER_SEC


class SimulatorSettings(BaseModel):
    mode: str
    rate: float
    latency_ms: int = 0


class ScenarioPreset(BaseModel):
    id: str
    label: str
    simulator: SimulatorSettings
    expects: str
    # Recovery also asks the Control Plane to lift quarantine; without it a
    # QUARANTINED endpoint receives no traffic and can never recover.
    restore: bool = False


SCENARIOS: Dict[str, ScenarioPreset] = {
    preset.id: preset
    for preset in (
        ScenarioPreset(
            id="normal",
            label="Normal",
            simulator=SimulatorSettings(mode="success", rate=0.0),
            expects="Every delivery succeeds; endpoint stays AVAILABLE.",
        ),
        ScenarioPreset(
            id="early_degradation",
            label="Early Degradation",
            simulator=SimulatorSettings(mode="mixed", rate=0.4, latency_ms=300),
            expects="Some 500s and slow responses; ERI drifts down.",
        ),
        ScenarioPreset(
            id="instability",
            label="Instability",
            simulator=SimulatorSettings(mode="http_error", rate=0.75),
            expects="Most deliveries return HTTP 500; backoff grows.",
        ),
        ScenarioPreset(
            id="full_outage",
            label="Full Outage",
            simulator=SimulatorSettings(
                mode="timeout", rate=1.0, latency_ms=TIMEOUT_LATENCY_MS
            ),
            expects="Every delivery times out; ERI collapses.",
        ),
        ScenarioPreset(
            id="recovery",
            label="Recovery",
            simulator=SimulatorSettings(mode="success", rate=0.0),
            expects="Endpoint healthy again; admin restore requested.",
            restore=True,
        ),
        ScenarioPreset(
            id="rate_limiting",
            label="Rate Limiting",
            simulator=SimulatorSettings(mode="rate_limit", rate=0.8),
            expects="Most deliveries return HTTP 429.",
        ),
        ScenarioPreset(
            id="auth_failure",
            label="Auth Failure",
            simulator=SimulatorSettings(mode="auth_failure", rate=1.0),
            expects="Every delivery returns HTTP 401.",
        ),
        ScenarioPreset(
            id="network_partition",
            label="Network Partition",
            simulator=SimulatorSettings(mode="connection_reset", rate=1.0),
            expects="Connections are dropped with no HTTP response.",
        ),
    )
}


class ActivateScenarioRequest(BaseModel):
    scenario: str


class DemoStatus(BaseModel):
    active_scenario: Optional[str] = None
    endpoint_id: Optional[str] = None
    subscription_id: Optional[str] = None
    simulator: Optional[SimulatorSettings] = None
    publishing: bool = False
    rate_per_sec: float = DEFAULT_RATE_PER_SEC
    events_published: int = 0
    publish_failures: int = 0
    last_error: Optional[str] = None


class DemoController:
    """Owns the single auto-publisher task and what it is currently doing."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self._status = DemoStatus()

    def status(self) -> DemoStatus:
        return self._status.model_copy(
            update={"publishing": self._task is not None and not self._task.done()}
        )

    async def activate(
        self,
        preset: ScenarioPreset,
        endpoint: Endpoint,
        subscription: Subscription,
        events: EventRepository,
    ) -> DemoStatus:
        async with self._lock:
            self._status = self._status.model_copy(
                update={
                    "active_scenario": preset.id,
                    "endpoint_id": endpoint.endpoint_id,
                    "subscription_id": subscription.subscription_id,
                    "simulator": preset.simulator,
                    "rate_per_sec": _rate_per_sec(),
                }
            )
            # One publisher for the life of the demo: switching scenario
            # only changes the simulator, never the traffic.
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._publish_loop(events))
        return self.status()

    async def stop(self) -> DemoStatus:
        async with self._lock:
            task, self._task = self._task, None
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._status = self._status.model_copy(update={"active_scenario": None})
        return self.status()

    async def _publish_loop(self, events: EventRepository) -> None:
        sequence = self._status.events_published
        while True:
            sequence += 1
            event = build_event(
                DEMO_EVENT_TYPE,
                {"sequence": sequence, "scenario": self._status.active_scenario},
            )
            try:
                await persist_and_publish(event, events)
                self._status = self._status.model_copy(
                    update={"events_published": self._status.events_published + 1}
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failed publish must not end the demo, but it must be
                # visible: logged here and counted in GET /demo/status.
                logger.exception(
                    "Demo publisher failed event_id=%s scenario=%s",
                    event.event_id,
                    self._status.active_scenario,
                )
                self._status = self._status.model_copy(
                    update={
                        "publish_failures": self._status.publish_failures + 1,
                        "last_error": f"{type(exc).__name__}: {exc}"[:300],
                    }
                )
            await asyncio.sleep(1.0 / self._status.rate_per_sec)


controller = DemoController()

router = APIRouter(prefix="/demo", tags=["demo"])


async def configure_simulator(settings: SimulatorSettings) -> None:
    async with httpx.AsyncClient(timeout=SIMULATOR_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{_simulator_url()}/simulator/config",
            json=settings.model_dump(),
        )
        response.raise_for_status()


async def ensure_demo_target(
    endpoints: EndpointRepository,
    subscriptions: SubscriptionRepository,
) -> tuple[Endpoint, Subscription]:
    """
    Find or create the demo endpoint and its subscription.

    Idempotent across restarts: Postgres keeps earlier rows, and a second
    active subscription to the same event type would deliver every demo
    event twice.
    """
    target_url = _target_url()
    endpoint = await endpoints.get_by_owner_and_target(DEMO_OWNER_ID, target_url)
    if endpoint is None:
        # Same egress rule as POST /endpoints; the demo is not a way around it.
        check_url(target_url)
        endpoint = Endpoint(target_url=target_url, owner_id=DEMO_OWNER_ID)
        await endpoints.save(endpoint)

    for existing in await subscriptions.list_active_by_event_type(DEMO_EVENT_TYPE):
        if existing.endpoint_id == endpoint.endpoint_id:
            return endpoint, existing

    subscription = Subscription(
        endpoint_id=endpoint.endpoint_id,
        event_types=[DEMO_EVENT_TYPE],
        status="ACTIVE",
    )
    await subscriptions.save(subscription)
    return endpoint, subscription


@router.get("/scenarios", response_model=List[ScenarioPreset])
async def list_scenarios(_owner_id: str = Depends(require_owner)) -> List[ScenarioPreset]:
    return list(SCENARIOS.values())


@router.get("/status", response_model=DemoStatus)
async def get_status(_owner_id: str = Depends(require_owner)) -> DemoStatus:
    return controller.status()


@router.post("/scenario", response_model=DemoStatus)
async def activate_scenario(
    body: ActivateScenarioRequest,
    _owner_id: str = Depends(require_owner),
    endpoints: EndpointRepository = Depends(get_endpoint_repository),
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
    events: EventRepository = Depends(get_event_repository),
) -> DemoStatus:
    preset = SCENARIOS.get(body.scenario)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": f"Unknown scenario '{body.scenario}'",
                "known": sorted(SCENARIOS),
            },
        )

    try:
        endpoint, subscription = await ensure_demo_target(endpoints, subscriptions)
    except UnsafeTargetURL as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DEMO_TARGET_URL is not a deliverable target: {exc}",
        ) from exc

    try:
        await configure_simulator(preset.simulator)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Simulator at {_simulator_url()} did not accept the scenario: {exc}",
        ) from exc

    if preset.restore:
        try:
            await publish_restore(endpoint.endpoint_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Simulator recovered, but publishing the restore request failed.",
                    "endpoint_id": endpoint.endpoint_id,
                },
            ) from exc

    return await controller.activate(preset, endpoint, subscription, events)


@router.post("/stop", response_model=DemoStatus)
async def stop_demo(_owner_id: str = Depends(require_owner)) -> DemoStatus:
    return await controller.stop()
