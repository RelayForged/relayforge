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
    # Empty: every event of `event_types`. Otherwise only events that share
    # at least one channel, which is how one tenant's events stay away from
    # another's endpoints (ADR 0025).
    channels: List[str] = Field(default_factory=list)

    def receives(self, event_channels: List[str]) -> bool:
        """The channel half of routing; the event type is matched separately."""
        return not self.channels or bool(set(self.channels) & set(event_channels))
