from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timezone
import uuid
from typing import List

class Subscription(BaseModel):
    """
    Links a class of events to a specific endpoint.
    """
    model_config = ConfigDict(frozen=True)

    subscription_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    endpoint_id: str
    event_types: List[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "ACTIVE"
