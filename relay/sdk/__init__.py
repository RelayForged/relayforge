"""Relay Python SDK — publish events, register endpoints, and manage subscriptions.

A standalone HTTP client for the Relay Event Delivery Platform.
"""
from relay.sdk.client import RelayClient
from relay.sdk.exceptions import RelayAPIError, RelayConnectionError, RelayError
from relay.sdk.models import (
    EndpointResponse,
    EventDetail,
    EventResponse,
    SubscriptionResponse,
)

__all__ = [
    "RelayClient",
    "RelayError",
    "RelayAPIError",
    "RelayConnectionError",
    "EventResponse",
    "EventDetail",
    "EndpointResponse",
    "SubscriptionResponse",
]
