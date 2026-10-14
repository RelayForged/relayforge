"""
Typed internal event models (domain only; not yet the Kafka payloads).

Person A's half of T13. Policy, endpoint and replay events are added by
Person B as separate modules that subclass `InternalEvent`.
"""
from relay.kernel.events.base import InternalEvent
from relay.kernel.events.delivery_events import (
    DeliveryAttemptCompleted,
    DeliveryAttemptStarted,
    DeliveryFailed,
)
from relay.kernel.events.operational_events import AlertTriggered, DeadLetterRouted

__all__ = [
    "InternalEvent",
    "DeliveryAttemptStarted",
    "DeliveryAttemptCompleted",
    "DeliveryFailed",
    "DeadLetterRouted",
    "AlertTriggered",
]
