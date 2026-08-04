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
