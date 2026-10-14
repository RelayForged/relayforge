from typing import Literal, Optional

from relay.kernel.events.base import InternalEvent


class DeliveryAttemptStarted(InternalEvent):
    """A delivery attempt row exists and the HTTP call is about to run."""
    delivery_id: str
    attempt_id: str
    attempt_number: int
    endpoint_id: str
    subscription_id: str


class DeliveryAttemptCompleted(InternalEvent):
    """A delivery attempt finished, successfully or not."""
    delivery_id: str
    attempt_id: str
    endpoint_id: str
    http_status: Optional[int] = None
    success: bool
    latency_ms: float
    timed_out: bool = False
    error_message: Optional[str] = None


class DeliveryFailed(InternalEvent):
    """A delivery reached a terminal failure status."""
    delivery_id: str
    endpoint_id: str
    business_event_id: str
    attempt_count: int
    status: Literal["FAILED", "DEAD_LETTER"]
