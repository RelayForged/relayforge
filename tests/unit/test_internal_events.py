"""Typed internal events: immutability, JSON round trip, kernel purity."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from relay.kernel.events import (
    AlertTriggered,
    DeadLetterRouted,
    DeliveryAttemptCompleted,
    DeliveryAttemptStarted,
    DeliveryFailed,
    InternalEvent,
)
from relay.kernel.value_objects.identifiers import CausationID, CorrelationID


def _base() -> dict:
    return {
        "correlation_id": CorrelationID(value="corr-1"),
        "causation_id": CausationID(value="cause-1"),
    }


def _samples() -> list[InternalEvent]:
    return [
        DeliveryAttemptStarted(
            **_base(), delivery_id="d1", attempt_id="a1", attempt_number=1,
            endpoint_id="ep1", subscription_id="s1",
        ),
        DeliveryAttemptCompleted(
            **_base(), delivery_id="d1", attempt_id="a1", endpoint_id="ep1",
            http_status=None, success=False, latency_ms=2000.0, timed_out=True,
            error_message="timeout",
        ),
        DeliveryFailed(
            **_base(), delivery_id="d1", endpoint_id="ep1",
            business_event_id="e1", attempt_count=10, status="FAILED",
        ),
        DeadLetterRouted(
            **_base(), delivery_id="d1", endpoint_id="ep1",
            business_event_id="e1", reason="PARKED",
        ),
        AlertTriggered(
            **_base(), endpoint_id="ep1", state="QUARANTINED", eri=18.3,
            reason="quarantine threshold crossed",
        ),
    ]


@pytest.mark.parametrize("event", _samples(), ids=lambda e: type(e).__name__)
def test_events_are_frozen(event):
    with pytest.raises(ValidationError):
        event.correlation_id = CorrelationID(value="other")
    first_field = next(iter(type(event).model_fields))
    with pytest.raises(ValidationError):
        setattr(event, first_field, getattr(event, first_field))


@pytest.mark.parametrize("event", _samples(), ids=lambda e: type(e).__name__)
def test_dict_round_trip(event):
    assert type(event).model_validate(event.model_dump()) == event


@pytest.mark.parametrize("event", _samples(), ids=lambda e: type(e).__name__)
def test_json_round_trip(event):
    assert type(event).model_validate_json(event.model_dump_json()) == event


def test_occurred_at_defaults_to_utc():
    event = _samples()[0]
    assert event.occurred_at.tzinfo is not None
    assert event.occurred_at.utcoffset().total_seconds() == 0


def test_status_and_reason_are_closed_sets():
    with pytest.raises(ValidationError):
        DeliveryFailed(
            **_base(), delivery_id="d", endpoint_id="e", business_event_id="b",
            attempt_count=1, status="PENDING",
        )
    with pytest.raises(ValidationError):
        DeadLetterRouted(
            **_base(), delivery_id="d", endpoint_id="e", business_event_id="b",
            reason="SUCCESS",
        )


def test_events_package_stays_on_kernel_dependencies():
    root = Path(__file__).resolve().parents[2] / "relay" / "kernel" / "events"
    banned = ("httpx", "aiokafka", "fastapi", "sqlalchemy")
    for path in root.glob("*.py"):
        text = path.read_text()
        for name in banned:
            assert f"import {name}" not in text and f"from {name}" not in text, (
                f"{path.name} imports {name}"
            )
