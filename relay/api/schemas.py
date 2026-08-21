"""
HTTP request/response models for the ingestion API.

These are boundary DTOs, not domain entities. Clients do not send
CorrelationID/CausationID -- the API constructs those when building Event.
"""
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class IngestEventRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any]


class IngestEventResponse(BaseModel):
    event_id: str


class CreateEndpointRequest(BaseModel):
    target_url: str
    owner_id: str


class CreateSubscriptionRequest(BaseModel):
    endpoint_id: str
    event_types: List[str] = Field(default_factory=list)
