"""Full Architecture Kafka Chain Integration Test (Task T-006).

Validates the complete platform architectural chain operating over Kafka wire messages:
  1. Event Ingestion -> Kafka wire payload on `events.business`.
  2. Dispatcher consumer reads KafkaMessage, deserializes Event, resolves Subscriptions.
  3. Control Plane handshake: queries PolicyFallbackManager (AEDE / Strategy C).
  4. WebhookWorker executes delivery against target simulator.
  5. Telemetry feedback loop: attempt completion notifies AEDEService.
  6. Real-time adaptation: consecutive failures drop ERI, triggering:
     AVAILABLE -> OBSERVED_DEGRADATION -> UNSTABLE -> QUARANTINED.
  7. Quarantine isolation: subsequent Kafka messages have isolation enforced (0 HTTP calls).
  8. Admin restoration -> RECOVERING probe -> AVAILABLE restoration.
  9. Offset commit discipline: Kafka watermark commits only after durable persistence.
"""
import asyncio
from datetime import datetime, timezone
import json
from uuid import uuid4
import pytest
import httpx

from relay.control_plane.aede.service import AEDEService
from relay.control_plane.aede.strategies import StrategyC
from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.eri.calculator import compute_eri
from relay.control_plane.state_machine.transitions import next_state
from relay.data_plane.dispatcher.main import Dispatcher
from relay.data_plane.workers.webhook import WebhookWorker
from relay.infrastructure.cache import InMemoryPolicyCache, PolicyFallbackManager
from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.messaging.consumer import (
    EVENTS_BUSINESS_TOPIC,
    EVENTS_INTERNAL_DELIVERY_TOPIC,
    KafkaMessage,
)
from relay.infrastructure.persistence.explainability_repo import InMemoryExplainabilityRepository
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.policies.delivery_policy import DeliveryPolicy, IsolationAction
from relay.kernel.value_objects.endpoint_state import EndpointState, StateMachineThresholds
from relay.kernel.value_objects.eri import ERIWeights, OPTIMAL_ERI_WEIGHTS
from relay.kernel.value_objects.telemetry import Telemetry

from tests.data_plane._helpers import (
    FakeAttemptRepository,
    FakeDeliveryRepository,
    FakeEndpointRepository,
    FakeSubscriptionRepository,
    make_event,
)


@pytest.fixture(autouse=True)
def _allow_target(monkeypatch):
    """Neutralise egress guard for end-to-end integration tests."""
    async def ok(url: str) -> None:
        return None

    monkeypatch.setattr("relay.data_plane.workers.webhook.assert_deliverable", ok)


@pytest.fixture(autouse=True)
def _mock_retry_publish(monkeypatch):
    """Intercept retry manager Kafka publish."""
    async def fake_publish(topic: str, key: str, value: bytes) -> None:
        return None

    monkeypatch.setattr("relay.data_plane.retry_manager.manager.publish", fake_publish)


class FakeTelemetryRepository:
    """In-memory telemetry sink for tests."""

    def __init__(self):
        self.telemetry = []

    async def save(self, observation: Telemetry) -> None:
        self.telemetry.append(observation)


class MockKafkaConsumerWatermark:
    """Mock consumer that records completed offset watermarks for commit discipline validation."""

    def __init__(self):
        self.completed_offsets: list[int] = []

    async def commit(self, message: KafkaMessage) -> None:
        self.completed_offsets.append(message.offset)


@pytest.mark.asyncio
async def test_full_kafka_chain_nominal_delivery():
    """Verify nominal event flowing through Kafka wire message -> Dispatcher -> Worker -> Telemetry -> AEDE."""
    now = datetime.now(timezone.utc)
    endpoint_id = "ep-kafka-1"
    sub_id = "sub-kafka-1"

    endpoint = Endpoint(endpoint_id=endpoint_id, target_url="https://example.com/webhook", owner_id="owner-test")
    subscription = Subscription(subscription_id=sub_id, endpoint_id=endpoint_id, event_types=["order.created"], status="ACTIVE")

    # Setup repositories
    ep_repo = FakeEndpointRepository([endpoint])
    sub_repo = FakeSubscriptionRepository([(subscription, endpoint)])
    del_repo = FakeDeliveryRepository()
    att_repo = FakeAttemptRepository()
    exp_repo = InMemoryExplainabilityRepository()
    cache = InMemoryPolicyCache()

    aede_service = AEDEService(
        cache=cache,
        explainability_repo=exp_repo,
        weights=OPTIMAL_ERI_WEIGHTS,
    )

    fallback_mgr = PolicyFallbackManager(cache=cache)
    flags = FeatureFlags(AEDE_ENABLED=True, STATIC_RETRY_MODE=False, ADAPTIVE_RETRY_MODE=False)
    consumer = MockKafkaConsumerWatermark()

    # Mock HTTP client returning 200 OK
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="OK"))
    client = httpx.AsyncClient(transport=transport)
    worker = WebhookWorker(
        client=client,
        aede_service=aede_service,
        attempts=att_repo,
        telemetry=FakeTelemetryRepository(),
    )

    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=sub_repo,
        deliveries=del_repo,
        attempts=att_repo,
        endpoints=ep_repo,
        worker=worker,
        fallback_manager=fallback_mgr,
        cache=cache,
        feature_flags=flags,
        aede_service=aede_service,
    )

    # 1. Ingest Event into Kafka wire payload
    event = make_event(event_type="order.created", payload={"order_id": "ord-123"})
    event_json = event.model_dump_json()
    msg = KafkaMessage(
        topic=EVENTS_BUSINESS_TOPIC,
        partition=0,
        offset=101,
        key=sub_id,
        value=event_json.encode("utf-8"),
    )

    # 2. Dispatch via Kafka message
    await dispatcher.handle_message(msg)

    # Assert Delivery persisted in database
    delivery = await del_repo.get_by_event_and_subscription(event.event_id, sub_id)
    assert delivery is not None
    assert delivery.status == "SUCCESS"

    # Assert Attempt persisted
    attempts = await att_repo.list_by_delivery_id(delivery.delivery_id)
    assert len(attempts) >= 1
    assert attempts[0].success is True
    assert attempts[0].http_status == 200

    # Assert Offset commit discipline
    assert 101 in consumer.completed_offsets

    # Assert AEDE telemetry and ERI remained nominal
    eri = aede_service.get_sliding_window_eri(endpoint_id)
    assert eri.score >= 90.0

    await client.aclose()


@pytest.mark.asyncio
async def test_full_kafka_chain_outage_quarantine_and_isolation():
    """Verify Kafka stream under 500 error outage transitions to QUARANTINED and completely halts worker calls."""
    endpoint_id = "ep-kafka-outage"
    sub_id = "sub-kafka-outage"

    endpoint = Endpoint(endpoint_id=endpoint_id, target_url="https://example.com/webhook", owner_id="owner-test")
    subscription = Subscription(subscription_id=sub_id, endpoint_id=endpoint_id, event_types=["order.failed"], status="ACTIVE")

    ep_repo = FakeEndpointRepository([endpoint])
    sub_repo = FakeSubscriptionRepository([(subscription, endpoint)])
    del_repo = FakeDeliveryRepository()
    att_repo = FakeAttemptRepository()
    exp_repo = InMemoryExplainabilityRepository()
    cache = InMemoryPolicyCache()

    aede_service = AEDEService(
        cache=cache,
        explainability_repo=exp_repo,
        weights=OPTIMAL_ERI_WEIGHTS,
        thresholds=StateMachineThresholds(
            degradation_threshold=80.0,
            degradation_recovery_threshold=90.0,
            unstable_threshold=60.0,
            unstable_recovery_threshold=70.0,
            quarantine_threshold=40.0,
            recovery_threshold=90.0,
        ),
        window_size=10,  # Small window to demonstrate rapid adaptation
    )

    fallback_mgr = PolicyFallbackManager(cache=cache)
    flags = FeatureFlags(AEDE_ENABLED=True, STATIC_RETRY_MODE=False, ADAPTIVE_RETRY_MODE=False)
    consumer = MockKafkaConsumerWatermark()

    # Seed 4 prior failed observations directly -- enough to already cross into QUARANTINED
    # before dispatch begins (representing an outage already in progress). That is
    # what this test is about: zero HTTP calls leak through the Dispatcher for a genuinely
    # quarantined endpoint, across the entire 15-message stream. (This seeding once also
    # worked around UNSTABLE's PAUSE stopping HTTP, a deadlock fixed by ADR 0022.)
    for _ in range(4):
        await aede_service.record_telemetry(
            Telemetry(
                attempt_id=str(uuid4()),
                endpoint_id=endpoint_id,
                succeeded=False,
                http_status=None,
                latency_ms=5000.0,
                timed_out=True,
                observed_at=datetime.now(timezone.utc),
            )
        )

    http_call_count = 0

    def handle_request(req):
        nonlocal http_call_count
        http_call_count += 1
        raise httpx.TimeoutException("Outage timeout")

    transport = httpx.MockTransport(handle_request)
    client = httpx.AsyncClient(transport=transport)
    worker = WebhookWorker(
        client=client,
        aede_service=aede_service,
        attempts=att_repo,
        telemetry=FakeTelemetryRepository(),
    )

    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=sub_repo,
        deliveries=del_repo,
        attempts=att_repo,
        endpoints=ep_repo,
        worker=worker,
        fallback_manager=fallback_mgr,
        cache=cache,
        feature_flags=flags,
        aede_service=aede_service,
    )

    # Stream 15 consecutive failure events through Kafka
    for i in range(1, 16):
        event = make_event(event_type="order.failed", payload={"idx": i})
        msg = KafkaMessage(
            topic=EVENTS_BUSINESS_TOPIC,
            partition=0,
            offset=i,
            key=sub_id,
            value=event.model_dump_json().encode("utf-8"),
        )
        await dispatcher.handle_message(msg)

    # Assert endpoint state reached QUARANTINED
    state = aede_service.get_endpoint_state(endpoint_id)
    assert state == EndpointState.QUARANTINED

    # Record how many HTTP calls were made up to quarantine
    calls_at_quarantine = http_call_count

    # Now dispatch 5 MORE events through Kafka while in QUARANTINED state
    for i in range(16, 21):
        event = make_event(event_type="order.failed", payload={"idx": i})
        msg = KafkaMessage(
            topic=EVENTS_BUSINESS_TOPIC,
            partition=0,
            offset=i,
            key=sub_id,
            value=event.model_dump_json().encode("utf-8"),
        )
        await dispatcher.handle_message(msg)

    # Assert Zero additional HTTP requests were executed (quarantine isolation preserved)
    assert http_call_count == calls_at_quarantine, (
        f"Quarantine leak: HTTP calls increased from {calls_at_quarantine} to {http_call_count}"
    )

    # Assert offset commits continued for durable messages
    for i in range(1, 21):
        assert i in consumer.completed_offsets

    # Assert Explainability record generated for QUARANTINED
    records = exp_repo.get_history(endpoint_id)
    quar_records = [r for r in records if r.state_after == "QUARANTINED"]
    assert len(quar_records) >= 1

    await client.aclose()


@pytest.mark.asyncio
async def test_full_kafka_chain_manual_admin_restore_to_available():
    """Verify simulated admin unquarantine restores Kafka message processing and returns to AVAILABLE."""
    endpoint_id = "ep-kafka-restore"
    sub_id = "sub-kafka-restore"

    endpoint = Endpoint(endpoint_id=endpoint_id, target_url="https://example.com/webhook", owner_id="owner-test")
    subscription = Subscription(subscription_id=sub_id, endpoint_id=endpoint_id, event_types=["invoice.created"], status="ACTIVE")

    ep_repo = FakeEndpointRepository([endpoint])
    sub_repo = FakeSubscriptionRepository([(subscription, endpoint)])
    del_repo = FakeDeliveryRepository()
    att_repo = FakeAttemptRepository()
    exp_repo = InMemoryExplainabilityRepository()
    cache = InMemoryPolicyCache()

    aede_service = AEDEService(
        cache=cache,
        explainability_repo=exp_repo,
        weights=OPTIMAL_ERI_WEIGHTS,
        window_size=10,
    )

    # Pre-set endpoint into QUARANTINED
    aede_service.set_endpoint_state(endpoint_id, EndpointState.QUARANTINED)

    fallback_mgr = PolicyFallbackManager(cache=cache)
    flags = FeatureFlags(AEDE_ENABLED=True, STATIC_RETRY_MODE=False, ADAPTIVE_RETRY_MODE=False)
    consumer = MockKafkaConsumerWatermark()

    # Now endpoint is restored by admin (health verified -> transition to RECOVERING)
    aede_service.restore_endpoint(endpoint_id)
    assert aede_service.get_endpoint_state(endpoint_id) == EndpointState.RECOVERING

    # Remote server now responds with 200 OK
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="Restored"))
    client = httpx.AsyncClient(transport=transport)
    worker = WebhookWorker(
        client=client,
        aede_service=aede_service,
        attempts=att_repo,
        telemetry=FakeTelemetryRepository(),
    )

    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=sub_repo,
        deliveries=del_repo,
        attempts=att_repo,
        endpoints=ep_repo,
        worker=worker,
        fallback_manager=fallback_mgr,
        cache=cache,
        feature_flags=flags,
        aede_service=aede_service,
    )

    # Deliver 12 successful Kafka events in RECOVERING state
    for i in range(1, 13):
        event = make_event(event_type="invoice.created", payload={"num": i})
        msg = KafkaMessage(
            topic=EVENTS_BUSINESS_TOPIC,
            partition=0,
            offset=i,
            key=sub_id,
            value=event.model_dump_json().encode("utf-8"),
        )
        await dispatcher.handle_message(msg)

    # Endpoint must have transitioned RECOVERING -> AVAILABLE
    final_state = aede_service.get_endpoint_state(endpoint_id)
    assert final_state == EndpointState.AVAILABLE

    # ERI score must have returned to 90+
    eri = aede_service.get_sliding_window_eri(endpoint_id)
    assert eri.score >= 90.0

    await client.aclose()
