"""
HTTP request/response models for the ingestion API.

These are boundary DTOs, not domain entities. Clients do not send
CorrelationID/CausationID -- the API constructs those when building Event.

Bounds are enforced here rather than on the entities: kernel models are
frozen and read-only for the data plane (ADR 0010), and an unbounded
`payload` would be written to JSONB and then re-serialized into a Kafka
record that can exceed the broker's max request size -- producing a
persisted event that can never be published.
"""
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from relay.infrastructure.url_guard import (
    MAX_TARGET_URL_LENGTH,
    UnsafeTargetURL,
    check_url,
)

# Kafka's default max.request.size is 1 MiB; stay well inside it so the
# serialized Event (payload + envelope) always fits.
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_EVENT_TYPE_LENGTH = 255
MAX_OWNER_ID_LENGTH = 255
MAX_EVENT_TYPES_PER_SUBSCRIPTION = 100


class IngestEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=MAX_EVENT_TYPE_LENGTH)
    payload: Dict[str, Any]

    @field_validator("payload")
    @classmethod
    def _bounded_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        encoded = len(
            json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
        )
        if encoded > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"payload is {encoded} bytes; limit is {MAX_PAYLOAD_BYTES}"
            )
        return value


class IngestEventResponse(BaseModel):
    event_id: str


class CreateEndpointRequest(BaseModel):
    target_url: str = Field(min_length=1, max_length=MAX_TARGET_URL_LENGTH)
    # Effective owner is the authenticated principal; this is only honored
    # in unauthenticated dev mode (see relay/api/security.py).
    owner_id: Optional[str] = Field(default=None, max_length=MAX_OWNER_ID_LENGTH)

    @field_validator("target_url")
    @classmethod
    def _safe_target(cls, value: str) -> str:
        try:
            check_url(value)
        except UnsafeTargetURL as exc:
            raise ValueError(str(exc)) from exc
        return value


class CreateSubscriptionRequest(BaseModel):
    endpoint_id: str = Field(min_length=1, max_length=64)
    event_types: List[str] = Field(default_factory=list)

    @field_validator("event_types")
    @classmethod
    def _bounded_event_types(cls, value: List[str]) -> List[str]:
        if len(value) > MAX_EVENT_TYPES_PER_SUBSCRIPTION:
            raise ValueError(
                f"at most {MAX_EVENT_TYPES_PER_SUBSCRIPTION} event_types allowed"
            )
        for event_type in value:
            if not event_type or len(event_type) > MAX_EVENT_TYPE_LENGTH:
                raise ValueError(
                    f"each event_type must be 1..{MAX_EVENT_TYPE_LENGTH} characters"
                )
        return value


class RestoreEndpointResponse(BaseModel):
    status: str
    endpoint_id: str
    target_state: str



class ReplayRequest(BaseModel):
    """
    Which dead letters to replay. With neither field set, the oldest
    `limit` dead letters across the caller's reach are replayed.
    """

    endpoint_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    delivery_ids: Optional[List[str]] = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=500)


class SkippedReplayResponse(BaseModel):
    delivery_id: str
    why: str


class ReplayResponse(BaseModel):
    replay_id: str
    requested: int
    skipped: List[SkippedReplayResponse] = Field(default_factory=list)
