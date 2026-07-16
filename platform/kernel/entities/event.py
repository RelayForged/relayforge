from pydantic import BaseModel, ConfigDict, Field
from typing import Dict, Any
import uuid
from datetime import datetime, timezone
from platform.kernel.value_objects.identifiers import CorrelationID, CausationID

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
