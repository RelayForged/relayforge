from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timezone
import uuid
from typing import Optional

class DeliveryAttempt(BaseModel):
    """
    A single execution of a Delivery Policy against an endpoint. 
    Each retry is a new, immutable DeliveryAttempt.
    """
    model_config = ConfigDict(frozen=True)

    attempt_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    delivery_id: str
    attempt_number: int
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    http_status: Optional[int] = None
    success: bool = False
    error_message: Optional[str] = None

class Delivery(BaseModel):
    """
    Represents the overall delivery process of an event to a subscription.
    """
    model_config = ConfigDict(frozen=True)

    delivery_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str
    subscription_id: str
    endpoint_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "PENDING"  # PENDING, SUCCESS, FAILED, DEAD_LETTER
