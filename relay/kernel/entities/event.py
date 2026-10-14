from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List
import uuid
from datetime import datetime, timezone
from relay.kernel.value_objects.identifiers import CorrelationID, CausationID

class Event(BaseModel):
    """
    An immutable business event flowing through the durable pipeline.
    """
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: CorrelationID
    causation_id: CausationID
    event_type: str
    payload: Dict[str, Any]
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Routing channels, e.g. the merchant an event belongs to (ADR 0025).
    # Only subscriptions with no channels, or sharing one, receive it.
    channels: List[str] = Field(default_factory=list)
