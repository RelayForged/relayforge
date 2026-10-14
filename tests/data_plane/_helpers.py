"""
In-memory doubles and builders for the Person A data-plane suite.
Fixtures that wrap these live in conftest.py.

These fakes exist so the suite runs with no Docker, no Postgres, and no
Kafka. That only works if they reproduce the *conflict semantics* of the
real repositories -- a fake that always inserts would let every test pass
while at-least-once redelivery corrupts production data. Each fake below
names the SQL behaviour it stands in for; keep them in sync with
relay/infrastructure/persistence/ when those change.

The end-to-end path against real Postgres and Kafka stays covered by
scripts/week34_smoke.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.policies.delivery_policy import (
    BackoffStrategy,
    ConcurrencyPolicy,
    DeliveryPolicy,
    IntegrityPolicy,
    IsolationAction,
    IsolationPolicy,
    RetryPolicy,
)
from relay.kernel.value_objects.telemetry import Telemetry


class FakeDeliveryRepository:
    """
    Mirrors DeliveryRepository.save:

        ON CONFLICT (event_id, subscription_id) DO UPDATE SET status
        WHERE deliveries.delivery_id = EXCLUDED.delivery_id
        RETURNING *

    So a redelivery carrying a fresh delivery_id is a no-op returning
    None, while a model_copy status transition on the canonical row
    applies and returns the updated row.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Delivery] = {}
        self.save_calls = 0

    async def save(self, delivery: Delivery) -> Optional[Delivery]:
        self.save_calls += 1
        key = (delivery.event_id, delivery.subscription_id)
        current = self.rows.get(key)
        if current is None:
            self.rows[key] = delivery
            return delivery
        if current.delivery_id != delivery.delivery_id:
            return None
        self.rows[key] = current.model_copy(update={"status": delivery.status})
        return self.rows[key]

    async def get_by_event_and_subscription(
        self, event_id: str, subscription_id: str
    ) -> Optional[Delivery]:
        return self.rows.get((event_id, subscription_id))

    async def get_by_id(self, delivery_id: str) -> Optional[Delivery]:
        return next(
            (r for r in self.rows.values() if r.delivery_id == delivery_id), None
        )

    def only(self) -> Delivery:
        assert len(self.rows) == 1, f"expected exactly one Delivery, got {len(self.rows)}"
        return next(iter(self.rows.values()))


class FakeAttemptRepository:
    """
    Mirrors DeliveryAttemptRepository, which has two distinct write paths
    because Postgres ON CONFLICT takes one target:

      insert_if_absent -> ON CONFLICT (delivery_id, attempt_number)
                          DO NOTHING RETURNING *
      save             -> ON CONFLICT (attempt_id) DO UPDATE
                          SET completion cols WHERE completed_at IS NULL

    Keyed on (delivery_id, attempt_number) since that is the natural key
    the unique constraint enforces.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], DeliveryAttempt] = {}

    async def insert_if_absent(
        self, attempt: DeliveryAttempt
    ) -> Optional[DeliveryAttempt]:
        key = (attempt.delivery_id, attempt.attempt_number)
        if key in self.rows:
            return None
        self.rows[key] = attempt
        return attempt

    async def save(self, attempt: DeliveryAttempt) -> Optional[DeliveryAttempt]:
        key = (attempt.delivery_id, attempt.attempt_number)
        current = self.rows.get(key)
        # The completion UPDATE is suppressed once completed_at is set --
        # this is also enforced by a DB trigger (ADR 0019).
        if current is not None and current.completed_at is not None:
            return None
        self.rows[key] = attempt
        return attempt

    async def get_by_delivery_and_number(
        self, delivery_id: str, attempt_number: int
    ) -> Optional[DeliveryAttempt]:
        return self.rows.get((delivery_id, attempt_number))

    async def get_by_id(self, attempt_id: str) -> Optional[DeliveryAttempt]:
        return next(
            (r for r in self.rows.values() if r.attempt_id == attempt_id), None
        )

    async def list_by_delivery_id(self, delivery_id: str) -> list[DeliveryAttempt]:
        return sorted(
            (r for (d, _), r in self.rows.items() if d == delivery_id),
            key=lambda r: r.attempt_number,
        )

    def numbers(self) -> list[int]:
        return sorted(number for _, number in self.rows)


class FakeTelemetryRepository:
    """Mirrors ON CONFLICT (attempt_id) DO NOTHING -- append-only."""

    def __init__(self) -> None:
        self.rows: dict[str, Telemetry] = {}
        self.save_calls = 0

    async def save(self, telemetry: Telemetry) -> None:
        self.save_calls += 1
        self.rows.setdefault(telemetry.attempt_id, telemetry)

    async def get_by_id(self, attempt_id: str) -> Optional[Telemetry]:
        return self.rows.get(attempt_id)

    def only(self) -> Telemetry:
        assert len(self.rows) == 1, f"expected one Telemetry, got {len(self.rows)}"
        return next(iter(self.rows.values()))


class FakeSubscriptionRepository:
    """Stands in for the joined fan-out query, honouring endpoint.is_active."""

    def __init__(self, targets: Optional[list[tuple[Subscription, Endpoint]]] = None):
        self.targets = targets or []
        self.calls: list[str] = []

    async def list_active_targets_by_event_type(
        self, event_type: str, channels: Optional[list[str]] = None
    ) -> list[tuple[Subscription, Endpoint]]:
        self.calls.append(event_type)
        return [
            (subscription, endpoint)
            for subscription, endpoint in self.targets
            if event_type in subscription.event_types
            and subscription.status == "ACTIVE"
            and endpoint.is_active
            and subscription.receives(list(channels or []))
        ]


class FakeEndpointRepository:
    def __init__(self, endpoints: Optional[list[Endpoint]] = None) -> None:
        self.rows = {e.endpoint_id: e for e in (endpoints or [])}
        self.lookups = 0

    async def get_by_id(self, endpoint_id: str) -> Optional[Endpoint]:
        self.lookups += 1
        return self.rows.get(endpoint_id)


class FakeConsumer:
    """
    Records commits instead of talking to Kafka. Watermark behaviour is
    exercised directly against the real _PartitionProgress in
    test_offset_commits.py; here we only need to know *whether* and *when*
    an offset was acknowledged.
    """

    def __init__(self) -> None:
        self.committed: list[int] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def commit(self, message) -> None:
        self.committed.append(message.offset)

    def pending_offsets(self) -> dict[str, int]:
        return {}

    async def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def make_event(event_type: str = "OrderCreated", **payload) -> Event:
    from relay.kernel.value_objects.identifiers import CausationID, CorrelationID

    correlation_id = CorrelationID()
    return Event(
        correlation_id=correlation_id,
        causation_id=CausationID(value=correlation_id.value),
        event_type=event_type,
        payload=payload or {"order_id": "test-1"},
    )


def make_policy(
    *,
    isolation: IsolationAction = IsolationAction.CONTINUE,
    max_parallel: int = 1,
    hmac_required: bool = False,
    replay_protection: bool = False,
    endpoint_id: str = "endpoint-1",
    **retry_overrides,
) -> DeliveryPolicy:
    """Strategy A's policy shape by default; overrides for edge cases."""
    retry = {
        "initial_delay_seconds": 5.0,
        "max_delay_seconds": 300.0,
        "max_retries": 10,
        "backoff_strategy": BackoffStrategy.EXPONENTIAL,
        "backoff_multiplier": 2.0,
    }
    retry.update(retry_overrides)
    return DeliveryPolicy(
        endpoint_id=endpoint_id,
        endpoint_state="AVAILABLE",
        eri_score=100.0,
        computed_at=datetime.now(timezone.utc),
        retry_policy=RetryPolicy(**retry),
        concurrency_policy=ConcurrencyPolicy(max_parallel=max_parallel),
        isolation_policy=IsolationPolicy(action=isolation),
        integrity_policy=IntegrityPolicy(
            hmac_required=hmac_required,
            replay_protection=replay_protection,
        ),
    )


def make_target(
    event_type: str = "OrderCreated",
    *,
    target_url: str = "https://hooks.example.test/webhook",
    is_active: bool = True,
) -> tuple[Subscription, Endpoint]:
    endpoint = Endpoint(target_url=target_url, owner_id="owner-1", is_active=is_active)
    subscription = Subscription(
        endpoint_id=endpoint.endpoint_id,
        event_types=[event_type],
        status="ACTIVE",
    )
    return subscription, endpoint


def completed_attempt(
    delivery: Delivery,
    attempt_number: int,
    *,
    success: bool = False,
    http_status: Optional[int] = 500,
) -> DeliveryAttempt:
    return DeliveryAttempt(
        delivery_id=delivery.delivery_id,
        attempt_number=attempt_number,
        success=success,
        http_status=http_status,
        completed_at=datetime.now(timezone.utc),
    )
