"""Relay Python SDK — async REST client for the Relay Ingestion API.

Same methods and exceptions as `RelayClient`, but awaitable, so it can be
used inside an `async def` route (for example an inbound webhook handler)
without blocking the event loop.

Usage:
    from relay.sdk import AsyncRelayClient

    async with AsyncRelayClient(api_key="...") as client:
        event = await client.publish_event("payment.captured", {"payment_id": "pay_1"})
"""
from typing import Any, Dict, List, Optional

import httpx

from relay.sdk._http import (
    build_headers,
    check_response,
    endpoint_from,
    event_detail_from,
    replay_from,
    subscription_from,
)
from relay.sdk.exceptions import RelayConnectionError
from relay.sdk.models import (
    EndpointResponse,
    EventDetail,
    EventResponse,
    ReplayResponse,
    SubscriptionResponse,
)


class AsyncRelayClient:
    """Asynchronous HTTP client for interacting with the Relay Ingestion API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """Initialize the AsyncRelayClient.

        Args:
            base_url: The base URL of the Relay FastAPI service.
            timeout: Request timeout in seconds.
            transport: Optional custom HTTPX async transport (tests/mocking).
            api_key: Sent as the X-API-Key header on every request. Required
                when the API runs with RELAY_API_KEYS set.
        """
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers=build_headers(api_key),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        expected_status: int,
    ) -> Dict[str, Any]:
        """Perform an HTTP request and handle network and API errors deterministically."""
        try:
            response = await self._http.request(method, path, json=json_body)
        except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as exc:
            raise RelayConnectionError(
                f"Failed to connect to Relay API at {self.base_url}: {exc}"
            ) from exc
        except Exception as exc:
            raise RelayConnectionError(
                f"Unexpected transport error connecting to Relay API: {exc}"
            ) from exc

        return check_response(response, expected_status)

    async def publish_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
    ) -> EventResponse:
        """Publish an outbound business event into Relay.

        Raises:
            RelayAPIError: If the API returns non-202 (e.g., 503 if Kafka publish fails).
            RelayConnectionError: If unable to reach the API.
        """
        data = await self._request(
            method="POST",
            path="/events",
            json_body={"event_type": event_type, "payload": payload},
            expected_status=202,
        )
        return EventResponse(event_id=data["event_id"])

    async def get_event(self, event_id: str) -> EventDetail:
        """Retrieve full details of an immutable event by ID."""
        data = await self._request(
            method="GET",
            path=f"/events/{event_id}",
            expected_status=200,
        )
        return event_detail_from(data)

    async def register_endpoint(
        self,
        target_url: str,
        owner_id: str,
    ) -> EndpointResponse:
        """Register a destination webhook endpoint."""
        data = await self._request(
            method="POST",
            path="/endpoints",
            json_body={"target_url": target_url, "owner_id": owner_id},
            expected_status=201,
        )
        return endpoint_from(data)

    async def create_subscription(
        self,
        endpoint_id: str,
        event_types: List[str],
    ) -> SubscriptionResponse:
        """Subscribe an endpoint to specific event types."""
        data = await self._request(
            method="POST",
            path="/subscriptions",
            json_body={"endpoint_id": endpoint_id, "event_types": event_types},
            expected_status=201,
        )
        return subscription_from(data)

    async def replay_dead_letters(
        self,
        endpoint_id: Optional[str] = None,
        delivery_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> ReplayResponse:
        """Request replay of failed, dead-lettered or parked deliveries.

        Replay never rewrites history: each replayed delivery is a new
        attempt chain, and the original records stay as they were.
        """
        body: Dict[str, Any] = {"limit": limit}
        if endpoint_id is not None:
            body["endpoint_id"] = endpoint_id
        if delivery_ids is not None:
            body["delivery_ids"] = delivery_ids
        data = await self._request(
            method="POST",
            path="/replay",
            json_body=body,
            expected_status=202,
        )
        return replay_from(data)

    async def aclose(self) -> None:
        """Close the underlying HTTP client session."""
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncRelayClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()
