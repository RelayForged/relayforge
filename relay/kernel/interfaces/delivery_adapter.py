"""
Delivery Adapter — the contract between a Worker and a transport.

A Worker knows what to send and what to record; an adapter knows how to
carry it over one transport (HTTP webhooks in v1). Keeping the contract
here, free of any transport library, is what lets another adapter be
added without touching the Worker.

An adapter performs exactly one send. It does not retry, sign, check
targets, or interpret the response: those belong to the policy, the
Worker and the Retry Manager.
"""
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class DeliveryResponse:
    """What the receiver answered. Any status is a response, including 4xx and 5xx."""

    status_code: int


class DeliveryTimeout(Exception):
    """The receiver did not answer within the adapter's time limit."""


class DeliveryTransportError(Exception):
    """The send failed below the application level: refused, reset, malformed target."""


@runtime_checkable
class DeliveryAdapter(Protocol):
    async def deliver(
        self, target_url: str, body: bytes, headers: Mapping[str, str]
    ) -> DeliveryResponse:
        """
        Send once. Returns the response for any HTTP status. Raises
        DeliveryTimeout or DeliveryTransportError when there is none.
        """
        ...

    async def aclose(self) -> None:
        """Release any resources the adapter owns."""
        ...
