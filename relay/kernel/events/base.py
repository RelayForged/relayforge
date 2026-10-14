from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from relay.kernel.value_objects.identifiers import CausationID, CorrelationID


class InternalEvent(BaseModel):
    """
    Base for every typed internal event.

    Immutable (ADR 0010). `correlation_id` ties the event to the business
    event whose delivery timeline it belongs to; `causation_id` names the
    event that caused this one. Subclasses add fields only: nothing here or
    below reads the clock (beyond the `occurred_at` default) or the environment.
    """
    model_config = ConfigDict(frozen=True)

    correlation_id: CorrelationID
    causation_id: CausationID
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
