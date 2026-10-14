"""Response handling shared by the sync and async SDK clients.

Kept free of any transport so `RelayClient` and `AsyncRelayClient` turn the
same HTTP response into the same result or the same exception. Depends only
on the SDK's own models and exceptions (no FastAPI, SQLAlchemy or Kafka).
"""
from typing import Any, Dict, Optional

import httpx

from relay.sdk.exceptions import RelayAPIError
from relay.sdk.models import (
    EndpointResponse,
    EventDetail,
    ReplayResponse,
    SubscriptionResponse,
)

# Same header the API reads in relay/api/security.py. Duplicated on purpose:
# the SDK is a standalone client and must not import the server package.
API_KEY_HEADER = "X-API-Key"


def build_headers(api_key: Optional[str]) -> Dict[str, str]:
    """Default headers for every request. Empty when no key is configured."""
    return {API_KEY_HEADER: api_key} if api_key else {}


def check_response(response: httpx.Response, expected_status: int) -> Dict[str, Any]:
    """Return the decoded JSON body, or raise RelayAPIError.

    Raises when the status is not `expected_status` or the body is not JSON.
    """
    if response.status_code != expected_status:
        detail = ""
        body_dict = None
        try:
            body_dict = response.json()
            if isinstance(body_dict, dict):
                detail = body_dict.get("detail", str(body_dict))
            else:
                detail = str(body_dict)
        except Exception:
            detail = response.text or f"Unexpected status {response.status_code}"

        raise RelayAPIError(
            status_code=response.status_code,
            detail=detail,
            response_body=body_dict if isinstance(body_dict, dict) else None,
        )

    try:
        return response.json()
    except Exception as exc:
        raise RelayAPIError(
            status_code=response.status_code,
            detail=f"Failed to decode JSON response: {exc}",
        ) from exc


def _id_value(raw: Any) -> str:
    """The API serialises CorrelationID/CausationID as {"value": ...}."""
    if isinstance(raw, dict):
        raw = raw.get("value", "")
    return str(raw)


def event_detail_from(data: Dict[str, Any]) -> EventDetail:
    return EventDetail(
        event_id=data["event_id"],
        event_type=data["event_type"],
        payload=data["payload"],
        occurred_at=str(data.get("occurred_at", "")),
        correlation_id=_id_value(data.get("correlation_id")),
        causation_id=_id_value(data.get("causation_id")),
    )


def endpoint_from(data: Dict[str, Any]) -> EndpointResponse:
    return EndpointResponse(
        endpoint_id=data["endpoint_id"],
        target_url=data["target_url"],
        owner_id=data["owner_id"],
        created_at=str(data.get("created_at", "")),
        is_active=data.get("is_active", True),
    )


def subscription_from(data: Dict[str, Any]) -> SubscriptionResponse:
    return SubscriptionResponse(
        subscription_id=data["subscription_id"],
        endpoint_id=data["endpoint_id"],
        event_types=data["event_types"],
        created_at=str(data.get("created_at", "")),
        status=data.get("status", "ACTIVE"),
    )


def replay_from(data: Dict[str, Any]) -> ReplayResponse:
    return ReplayResponse(
        replay_id=data["replay_id"],
        requested=data["requested"],
        skipped=data.get("skipped", []),
    )
