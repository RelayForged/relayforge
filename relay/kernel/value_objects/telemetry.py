from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class Telemetry(BaseModel):
    """
    An immutable observation of a single delivery attempt.
    """
    model_config = ConfigDict(frozen=True)

    attempt_id: str = Field(description="The ID of the DeliveryAttempt this reading belongs to.")
    endpoint_id: str = Field(description="The ID of the endpoint that was targeted.")
    succeeded: bool = Field(description="Whether the delivery succeeded (typically HTTP 2xx).")
    http_status: Optional[int] = Field(default=None, description="HTTP response code. None if failed before response.")
    latency_ms: float = Field(description="Response time in milliseconds.")
    timed_out: bool = Field(default=False, description="True if the attempt timed out.")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When this observation was recorded.")

    @property
    def is_http_5xx(self) -> bool:
        return self.http_status is not None and 500 <= self.http_status <= 599
