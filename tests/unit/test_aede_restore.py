"""Operator restore on the live path (ADR 0020)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from relay.control_plane.aede.service import AEDEService
from relay.infrastructure.cache import InMemoryPolicyCache
from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_POLICY_TOPIC
from relay.infrastructure.persistence.explainability_repo import (
    InMemoryExplainabilityRepository,
)
from relay.kernel.policies.delivery_policy import IsolationAction
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.telemetry import Telemetry

ENDPOINT = "ep-restore"
T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _telemetry(i: int, at: datetime, *, ok: bool, latency_ms: float = 80.0) -> Telemetry:
    return Telemetry(
        attempt_id=f"attempt-{i}",
        endpoint_id=ENDPOINT,
        succeeded=ok,
        http_status=200 if ok else None,
        latency_ms=latency_ms,
        timed_out=not ok,
        observed_at=at,
    )


class Harness:
    def __init__(self) -> None:
        self.cache = InMemoryPolicyCache()
        self.records = InMemoryExplainabilityRepository()
        self.published: list[tuple[str, str, dict]] = []
        self.service = AEDEService(
            cache=self.cache,
            explainability_repo=self.records,
            publisher=self._publish,
        )

    def _publish(self, topic: str, key: str, value: bytes) -> None:
        self.published.append((topic, key, json.loads(value)))

    async def quarantine(self) -> datetime:
        """Drive the endpoint into QUARANTINED with real failing telemetry."""
        at = T0
        for i in range(60):
            at = T0 + timedelta(seconds=i)
            await self.service.record_telemetry(
                _telemetry(i, at, ok=False, latency_ms=2000.0)
            )
            if self.service.get_endpoint_state(ENDPOINT) == EndpointState.QUARANTINED:
                return at
        raise AssertionError("endpoint never reached QUARANTINED")


@pytest.fixture
async def quarantined() -> tuple[Harness, datetime]:
    harness = Harness()
    at = await harness.quarantine()
    harness.published.clear()
    return harness, at


async def test_restore_replaces_cached_quarantine_policy(quarantined) -> None:
    harness, at = quarantined
    assert (
        harness.service.get_current_policy(ENDPOINT).isolation_policy.action
        == IsolationAction.QUARANTINE
    )

    state = await harness.service.restore_and_publish(
        ENDPOINT, restored_at=at + timedelta(seconds=30)
    )

    assert state == EndpointState.RECOVERING
    policy = harness.service.get_current_policy(ENDPOINT)
    assert policy.endpoint_state == EndpointState.RECOVERING.value
    assert policy.isolation_policy.action == IsolationAction.CONTINUE


async def test_restore_records_and_publishes_the_transition(quarantined) -> None:
    harness, at = quarantined
    restored_at = at + timedelta(seconds=30)

    await harness.service.restore_and_publish(ENDPOINT, restored_at=restored_at)

    record = harness.records.get_latest_by_endpoint(ENDPOINT)
    assert record.state_before == "QUARANTINED"
    assert record.state_after == "RECOVERING"
    assert record.state_changed is True
    assert "Admin restore" in record.reason
    assert record.evaluated_at == restored_at

    assert len(harness.published) == 1
    topic, key, bundle = harness.published[0]
    assert topic == EVENTS_INTERNAL_POLICY_TOPIC
    assert key == ENDPOINT
    assert bundle["state"] == "RECOVERING"
    assert bundle["policy_evaluation"]["state_before"] == "QUARANTINED"
    assert bundle["policy_evaluation"]["state_changed"] is True
    assert bundle["explainability_record"]["record_id"] == record.record_id


async def test_first_healthy_probe_after_restore_does_not_requarantine(quarantined) -> None:
    harness, at = quarantined
    restored_at = at + timedelta(seconds=30)
    await harness.service.restore_and_publish(ENDPOINT, restored_at=restored_at)

    _, state, _, _ = await harness.service.record_telemetry(
        _telemetry(100, restored_at + timedelta(seconds=1), ok=True)
    )

    assert state != EndpointState.QUARANTINED


async def test_attempt_in_flight_at_restore_is_excluded(quarantined) -> None:
    harness, at = quarantined
    restored_at = at + timedelta(seconds=30)
    await harness.service.restore_and_publish(ENDPOINT, restored_at=restored_at)
    harness.published.clear()

    # Started 1.5 s before the restore, timed out 0.5 s after it.
    stale = _telemetry(
        200, restored_at + timedelta(seconds=0.5), ok=False, latency_ms=2000.0
    )
    eri, state, _, _ = await harness.service.record_telemetry(stale)

    assert state == EndpointState.RECOVERING
    assert eri.window_size == 0
    assert harness.published == [], "stale telemetry must not emit an evaluation"


async def test_failure_after_restore_still_requarantines(quarantined) -> None:
    harness, at = quarantined
    restored_at = at + timedelta(seconds=30)
    await harness.service.restore_and_publish(ENDPOINT, restored_at=restored_at)

    state = EndpointState.RECOVERING
    for i in range(10):
        _, state, _, _ = await harness.service.record_telemetry(
            _telemetry(
                300 + i,
                restored_at + timedelta(seconds=5 + i),
                ok=False,
                latency_ms=2000.0,
            )
        )

    assert state == EndpointState.QUARANTINED


async def test_restore_is_a_noop_when_not_quarantined() -> None:
    harness = Harness()
    await harness.service.record_telemetry(_telemetry(1, T0, ok=True))
    harness.published.clear()
    before = harness.records.get_history(ENDPOINT)

    state = await harness.service.restore_and_publish(
        ENDPOINT, restored_at=T0 + timedelta(seconds=5)
    )

    assert state == EndpointState.AVAILABLE
    assert harness.published == []
    assert harness.records.get_history(ENDPOINT) == before
    assert harness.service.get_sliding_window_eri(ENDPOINT, computed_at=T0).window_size == 1


async def test_state_only_restore_keeps_the_window_for_benchmark_parity(quarantined) -> None:
    harness, at = quarantined
    window_before = harness.service.get_sliding_window_eri(
        ENDPOINT, computed_at=at
    ).window_size

    state = harness.service.restore_endpoint(ENDPOINT)

    assert state == EndpointState.RECOVERING
    assert (
        harness.service.get_sliding_window_eri(ENDPOINT, computed_at=at).window_size
        == window_before
    )
    assert harness.published == []
