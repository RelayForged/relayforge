from typing import Literal

from relay.kernel.events.base import InternalEvent


class DeadLetterRouted(InternalEvent):
    """A delivery was counted as dead-lettered (ADR 0021)."""
    delivery_id: str
    endpoint_id: str
    business_event_id: str
    reason: Literal["FAILED", "DEAD_LETTER", "PARKED"]


class AlertTriggered(InternalEvent):
    """An endpoint crossed into a state that warrants operator attention."""
    endpoint_id: str
    state: str
    eri: float
    reason: str
