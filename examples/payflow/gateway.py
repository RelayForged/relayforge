"""
PayFlow's integration with Relay: everything a payment gateway's backend
needs to notify its merchants of payments.

This is the code to show on a slide. Two calls onboard a merchant, one call
notifies them of a payment, and two calls recover a merchant after an
outage. Channels keep each merchant's payments away from every other
merchant's endpoint.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Optional

from relay.sdk import RelayClient

RELAY_URL = os.environ.get("RELAY_URL", "http://localhost:8000")
RELAY_API_KEY = os.environ.get("RELAY_API_KEY") or None

PAYMENT_EVENTS = ["payment.captured", "payment.failed"]


def relay() -> RelayClient:
    return RelayClient(base_url=RELAY_URL, api_key=RELAY_API_KEY)


@dataclass(frozen=True)
class MerchantWebhook:
    merchant_id: str
    endpoint_id: str
    subscription_id: str


def onboard_merchant(merchant_id: str, webhook_url: str) -> MerchantWebhook:
    """A merchant saved a webhook URL in PayFlow's dashboard."""
    with relay() as client:
        endpoint = client.register_endpoint(webhook_url, owner_id="payflow")
        subscription = client.create_subscription(
            endpoint.endpoint_id,
            PAYMENT_EVENTS,
            channels=[merchant_id],  # only this merchant's payments
        )
    return MerchantWebhook(merchant_id, endpoint.endpoint_id, subscription.subscription_id)


def notify_payment(
    merchant_id: str,
    amount: float,
    *,
    customer: str,
    method: str,
    failed: bool = False,
    payment_id: Optional[str] = None,
) -> str:
    """A customer paid (or the payment failed). Returns Relay's event id."""
    with relay() as client:
        event = client.publish_event(
            "payment.failed" if failed else "payment.captured",
            {
                "payment_id": payment_id or f"pay_{secrets.token_hex(7)}",
                "merchant_id": merchant_id,
                "amount": round(amount, 2),
                "currency": "INR",
                "method": method,
                "customer": customer,
            },
            channels=[merchant_id],
        )
    return event.event_id


def recover_merchant(endpoint_id: str) -> int:
    """The merchant is back: lift the quarantine and resend what it missed."""
    with relay() as client:
        client.restore_endpoint(endpoint_id)
        replay = client.replay_dead_letters(endpoint_id=endpoint_id, limit=500)
    return replay.requested
