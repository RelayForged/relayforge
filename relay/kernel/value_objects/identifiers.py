from pydantic import BaseModel, ConfigDict, Field
import uuid

class CorrelationID(BaseModel):
    """
    Propagated across all internal events for a given business event. 
    Enables complete delivery timeline reconstruction.
    """
    model_config = ConfigDict(frozen=True)
    value: str = Field(default_factory=lambda: str(uuid.uuid4()))

class CausationID(BaseModel):
    """
    Tracks the causality between events (which event caused this event).
    """
    model_config = ConfigDict(frozen=True)
    value: str


# Fixed namespace for ids derived from a source delivery (ADR 0021).
_REPLAY_NAMESPACE = uuid.UUID("6f1c2f0e-7d1b-5c1a-9f58-2b6f0f6f3c21")


def replay_event_id(source_delivery_id: str) -> str:
    """
    The id of the Event that replays a given delivery.

    Deterministic, so requesting the same replay twice (a double click, a
    redelivered Kafka message) yields the same Event rather than a second
    one, and every downstream write stays idempotent.
    """
    return str(uuid.uuid5(_REPLAY_NAMESPACE, source_delivery_id))
