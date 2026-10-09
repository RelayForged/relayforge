"""Integration tests for the Closed Telemetry Feedback Loop (Task T-003).

Validates the full Control Plane feedback cycle:
WebhookWorker Attempt Completion -> Telemetry Ingestion -> Sliding Window ERI Recomputation ->
State Machine Transition -> Policy Synthesis -> Policy Cache Refresh -> Explainability Persistence.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
import httpx

from relay.control_plane.aede.service import AEDEService
from relay.data_plane.workers.webhook import WebhookWorker
from relay.infrastructure.cache import InMemoryPolicyCache
from relay.infrastructure.persistence.explainability_repo import (
    InMemoryExplainabilityRepository,
)
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.event import Event
from relay.kernel.policies.delivery_policy import (
    DeliveryPolicy,
    IsolationAction,
    RetryPolicy,
    ConcurrencyPolicy,
    IsolationPolicy,
    ReplayPolicy,
    NotificationPolicy,
    IntegrityPolicy,
)
from relay.kernel.value_objects.endpoint_state import EndpointState, StateMachineThresholds
from relay.kernel.value_objects.eri import ERIWeights
from relay.kernel.value_objects.telemetry import Telemetry


@pytest.fixture(autouse=True)
def _allow_target(monkeypatch):
    """Neutralise the egress guard for contract/integration tests."""
    async def ok(url: str) -> None:
        return None

    monkeypatch.setattr("relay.data_plane.workers.webhook.assert_deliverable", ok)


class FakeAttemptRepository:
    def __init__(self):
        self.attempts = []

    async def save(self, attempt: DeliveryAttempt) -> None:
        self.attempts.append(attempt)


class FakeTelemetryRepository:
    def __init__(self):
        self.telemetry = []

    async def save(self, observation: Telemetry) -> None:
        self.telemetry.append(observation)


from tests.data_plane._helpers import make_event, make_policy


def make_delivery_context(endpoint_id: str = "ep-test-loop"):
    event = make_event("OrderCreated", order_id="ord-123")
    delivery = Delivery(
        delivery_id=str(uuid4()),
        event_id=event.event_id,
        subscription_id=str(uuid4()),
        endpoint_id=endpoint_id,
        created_at=datetime.now(timezone.utc),
    )
    attempt = DeliveryAttempt(
        attempt_id=str(uuid4()),
        delivery_id=delivery.delivery_id,
        attempt_number=1,
        started_at=datetime.now(timezone.utc),
    )
    policy = make_policy(endpoint_id=endpoint_id)
    return event, delivery, attempt, policy


@pytest.mark.asyncio
async def test_aede_service_telemetry_loop_and_state_degradation():
    """Verify telemetry ingestion updates ERI and triggers state degradation."""
    cache = InMemoryPolicyCache()
    explain_repo = InMemoryExplainabilityRepository()
    service = AEDEService(
        cache=cache,
        explainability_repo=explain_repo,
        weights=ERIWeights.optimal(),
        window_size=20,
    )
    endpoint_id = "ep-state-degrade"
    now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Ingest 10 healthy successful attempts -> state should be AVAILABLE
    for i in range(10):
        t = Telemetry(
            attempt_id=f"att-success-{i}",
            endpoint_id=endpoint_id,
            succeeded=True,
            http_status=200,
            latency_ms=80.0,
            timed_out=False,
            observed_at=now + timedelta(seconds=i),
        )
        eri, state, policy, record = await service.record_telemetry(t)
        assert state == EndpointState.AVAILABLE
        assert eri.score == 100.0
        assert policy.isolation_policy.action == IsolationAction.CONTINUE

    # 2. Ingest 15 severe failure attempts -> state should degrade to UNSTABLE / QUARANTINED
    for i in range(15):
        t = Telemetry(
            attempt_id=f"att-fail-{i}",
            endpoint_id=endpoint_id,
            succeeded=False,
            http_status=500,
            latency_ms=3000.0,
            timed_out=True,
            observed_at=now + timedelta(seconds=10 + i),
        )
        eri, state, policy, record = await service.record_telemetry(t)

    assert state in (EndpointState.UNSTABLE, EndpointState.QUARANTINED)
    assert eri.score < 60.0

    # 3. Check PolicyCache was refreshed with latest policy
    cached_policy = cache.get_policy(endpoint_id)
    assert cached_policy is not None
    assert cached_policy.endpoint_state == state.value
    assert cached_policy.eri_score == eri.score

    # 4. Check ExplainabilityRecords were recorded
    latest_record = explain_repo.get_latest_by_endpoint(endpoint_id)
    assert latest_record is not None
    assert latest_record.endpoint_id == endpoint_id
    assert latest_record.state_after == state.value


@pytest.mark.asyncio
async def test_webhook_worker_notifies_aede_service():
    """Verify WebhookWorker.execute() automatically triggers AEDEService feedback loop."""
    cache = InMemoryPolicyCache()
    explain_repo = InMemoryExplainabilityRepository()
    service = AEDEService(cache=cache, explainability_repo=explain_repo)

    attempt_repo = FakeAttemptRepository()
    telemetry_repo = FakeTelemetryRepository()

    # Mock client that responds with HTTP 200
    mock_transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code=200, json={"received": True})
    )
    async with httpx.AsyncClient(transport=mock_transport) as client:
        worker = WebhookWorker(
            attempts=attempt_repo,
            telemetry=telemetry_repo,
            client=client,
            aede_service=service,
        )

        endpoint_id = "ep-worker-feed"
        event, delivery, attempt, policy = make_delivery_context(endpoint_id)

        completed_attempt, observation = await worker.execute(
            attempt=attempt,
            target_url="https://api.example.com/webhook",
            endpoint_id=endpoint_id,
            event=event,
            delivery=delivery,
            policy=policy,
        )

        # Worker persisted attempt and telemetry
        assert completed_attempt.success is True
        assert len(telemetry_repo.telemetry) == 1
        assert telemetry_repo.telemetry[0].attempt_id == attempt.attempt_id

        # AEDEService was notified: cache has policy, explainability record stored
        cached = cache.get_policy(endpoint_id)
        assert cached is not None
        assert cached.endpoint_id == endpoint_id

        record = explain_repo.get_latest_by_endpoint(endpoint_id)
        assert record is not None
        assert record.endpoint_id == endpoint_id
        assert record.state_after == "AVAILABLE"


@pytest.mark.asyncio
async def test_aede_service_manual_restoration():
    """Verify admin manual restoration transitions QUARANTINED -> RECOVERING -> AVAILABLE."""
    cache = InMemoryPolicyCache()
    explain_repo = InMemoryExplainabilityRepository()
    service = AEDEService(
        cache=cache,
        explainability_repo=explain_repo,
        weights=ERIWeights.optimal(),
        window_size=10,
    )
    endpoint_id = "ep-quarantine-restore"
    now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)

    # Force endpoint into QUARANTINED
    service.set_endpoint_state(endpoint_id, EndpointState.QUARANTINED)
    assert service.get_endpoint_state(endpoint_id) == EndpointState.QUARANTINED

    # Admin verifies health and performs manual restoration
    restored_state = service.restore_endpoint(endpoint_id)
    assert restored_state == EndpointState.RECOVERING
    assert service.get_endpoint_state(endpoint_id) == EndpointState.RECOVERING

    # Now ingest a series of healthy recoveries
    for i in range(12):
        t = Telemetry(
            attempt_id=f"att-heal-{i}",
            endpoint_id=endpoint_id,
            succeeded=True,
            http_status=200,
            latency_ms=75.0,
            timed_out=False,
            observed_at=now + timedelta(seconds=i),
        )
        eri, state, policy, record = await service.record_telemetry(t)

    # After sufficient healthy attempts with ERI > 90.0, state recovers to AVAILABLE
    assert service.get_endpoint_state(endpoint_id) == EndpointState.AVAILABLE
    assert cache.get_policy(endpoint_id).endpoint_state == EndpointState.AVAILABLE.value
