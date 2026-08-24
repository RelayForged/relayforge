"""Lightweight SDK response models.

These are pure dataclasses with frozen=True, free from internal Relay framework dependencies
(e.g., Pydantic v2, SQLAlchemy, Kafka).
"""
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class EventResponse:
    """Returned by publish_event() upon 202 Accepted ingestion."""
    event_id: str


@dataclass(frozen=True)
class EventDetail:
    """Returned by get_event() upon retrieving an event by ID."""
    event_id: str
    event_type: str
    payload: Dict[str, Any]
    occurred_at: str
    correlation_id: str
    causation_id: str


@dataclass(frozen=True)
class EndpointResponse:
    """Returned by register_endpoint() upon 201 Created."""
    endpoint_id: str
    target_url: str
    owner_id: str
    created_at: str
    is_active: bool


@dataclass(frozen=True)
class SubscriptionResponse:
    """Returned by create_subscription() upon 201 Created."""
    subscription_id: str
    endpoint_id: str
    event_types: List[str]
    created_at: str
    status: str
