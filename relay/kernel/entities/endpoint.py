from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timezone
import uuid
from typing import Optional

class Endpoint(BaseModel):
    """
    A webhook destination endpoint.
    """
    model_config = ConfigDict(frozen=True)

    endpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_url: str
    owner_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
