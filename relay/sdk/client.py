"""Relay Python SDK — thin REST client for the Relay Ingestion API.

Usage:
    from relay.sdk import RelayClient

    client = RelayClient()  # defaults to http://localhost:8000
    endpoint = client.register_endpoint("http://my-service/webhook", "team-a")
    sub = client.create_subscription(endpoint.endpoint_id, ["OrderCreated"])
    event = client.publish_event("OrderCreated", {"order_id": "123"})
    print(f"Ingested event: {event.event_id}")
    client.close()

Or as a context manager:
    with RelayClient() as client:
        client.publish_event("OrderCreated", {"order_id": "123"})
"""
from typing import Any, Dict, List, Optional
import httpx

from relay.sdk.exceptions import RelayAPIError, RelayConnectionError
from relay.sdk.models import (
    EndpointResponse,
    EventDetail,
    EventResponse,
    ReplayResponse,
    SubscriptionResponse,
)


class RelayClient:
    """Synchronous HTTP client for interacting with the Relay Ingestion API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 10.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """Initialize the RelayClient.

        Args:
            base_url: The base URL of the Relay FastAPI service.
            timeout: Request timeout in seconds.
            transport: Optional custom HTTPX transport (useful for tests/mocking).
        """
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        expected_status: int,
    ) -> Dict[str, Any]:
        """Perform an HTTP request and handle network and API errors deterministically."""
        try:
            response = self._http.request(method, path, json=json_body)
        except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as exc:
            raise RelayConnectionError(f"Failed to connect to Relay API at {self.base_url}: {exc}") from exc
        except Exception as exc:
            raise RelayConnectionError(f"Unexpected transport error connecting to Relay API: {exc}") from exc

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

    def publish_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
    ) -> EventResponse:
        """Publish an outbound business event into Relay.

        Args:
            event_type: Domain event classification (e.g., 'OrderCreated').
            payload: Arbitrary business payload dictionary.

        Returns:
            EventResponse with the assigned event_id.

        Raises:
            RelayAPIError: If the API returns non-202 (e.g., 503 if Kafka publish fails).
            RelayConnectionError: If unable to reach the API.
        """
        data = self._request(
            method="POST",
            path="/events",
            json_body={"event_type": event_type, "payload": payload},
            expected_status=202,
        )
        return EventResponse(event_id=data["event_id"])

    def get_event(self, event_id: str) -> EventDetail:
        """Retrieve full details of an immutable event by ID.

        Args:
            event_id: The UUID of the event.

        Returns:
            EventDetail with complete event attributes.

        Raises:
            RelayAPIError: If event not found (404) or on other API errors.
            RelayConnectionError: If unable to reach the API.
        """
        data = self._request(
            method="GET",
            path=f"/events/{event_id}",
            expected_status=200,
        )
        correlation_id = data.get("correlation_id")
        if isinstance(correlation_id, dict):
            correlation_id = correlation_id.get("value", "")

        causation_id = data.get("causation_id")
        if isinstance(causation_id, dict):
            causation_id = causation_id.get("value", "")

        return EventDetail(
            event_id=data["event_id"],
            event_type=data["event_type"],
            payload=data["payload"],
            occurred_at=str(data.get("occurred_at", "")),
            correlation_id=str(correlation_id),
            causation_id=str(causation_id),
        )

    def register_endpoint(
        self,
        target_url: str,
        owner_id: str,
    ) -> EndpointResponse:
        """Register a destination webhook endpoint.

        Args:
            target_url: Destination HTTP URL for webhook deliveries.
            owner_id: Identifier of the owner team/service.

        Returns:
            EndpointResponse with the created endpoint properties.

        Raises:
            RelayAPIError: If endpoint creation fails.
            RelayConnectionError: If unable to reach the API.
        """
        data = self._request(
            method="POST",
            path="/endpoints",
            json_body={"target_url": target_url, "owner_id": owner_id},
            expected_status=201,
        )
        return EndpointResponse(
            endpoint_id=data["endpoint_id"],
            target_url=data["target_url"],
            owner_id=data["owner_id"],
            created_at=str(data.get("created_at", "")),
            is_active=data.get("is_active", True),
        )

    def create_subscription(
        self,
        endpoint_id: str,
        event_types: List[str],
    ) -> SubscriptionResponse:
        """Subscribe an endpoint to specific event types.

        Args:
            endpoint_id: The UUID of the destination endpoint.
            event_types: List of event type strings to deliver to this endpoint.

        Returns:
            SubscriptionResponse with subscription ID and status.

        Raises:
            RelayAPIError: If endpoint not found (404) or on invalid input.
            RelayConnectionError: If unable to reach the API.
        """
        data = self._request(
            method="POST",
            path="/subscriptions",
            json_body={"endpoint_id": endpoint_id, "event_types": event_types},
            expected_status=201,
        )
        return SubscriptionResponse(
            subscription_id=data["subscription_id"],
            endpoint_id=data["endpoint_id"],
            event_types=data["event_types"],
            created_at=str(data.get("created_at", "")),
            status=data.get("status", "ACTIVE"),
        )

    def replay_dead_letters(
        self,
        endpoint_id: Optional[str] = None,
        delivery_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> ReplayResponse:
        """Request replay of failed, dead-lettered or parked deliveries.

        Replay never rewrites history: each replayed delivery is a new
        attempt chain, and the original records stay as they were.

        Args:
            endpoint_id: Replay only this endpoint's dead letters.
            delivery_ids: Replay only these deliveries.
            limit: Maximum number of deliveries to replay (oldest first).

        Returns:
            ReplayResponse with the replay id, how many were requested, and
            any deliveries that were skipped with the reason.

        Raises:
            RelayAPIError: If the endpoint is unknown (404) or the request fails.
            RelayConnectionError: If unable to reach the API.
        """
        body: Dict[str, Any] = {"limit": limit}
        if endpoint_id is not None:
            body["endpoint_id"] = endpoint_id
        if delivery_ids is not None:
            body["delivery_ids"] = delivery_ids
        data = self._request(
            method="POST",
            path="/replay",
            json_body=body,
            expected_status=202,
        )
        return ReplayResponse(
            replay_id=data["replay_id"],
            requested=data["requested"],
            skipped=data.get("skipped", []),
        )

    def close(self) -> None:
        """Close the underlying HTTP client session."""
        self._http.close()

    def __enter__(self) -> "RelayClient":
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context manager and close the client session."""
        self.close()
