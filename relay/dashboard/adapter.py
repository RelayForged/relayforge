from datetime import datetime, timezone

from .models import (
    DeliveryEvent,
    ERIPoint,
    PolicyDecision,
    PolicyExplanation,
)


DEMO_DELIVERY_EVENTS: list[DeliveryEvent] = [
    {
        "id": 1,
        "timestamp": "12:34:01",
        "type": "REQUEST_RECEIVED",
    },
    {
        "id": 2,
        "timestamp": "12:34:02",
        "type": "ROUTE_SELECTED",
    },
    {
        "id": 3,
        "timestamp": "12:34:03",
        "type": "DELIVERY_ATTEMPT",
    },
    {
        "id": 4,
        "timestamp": "12:34:04",
        "type": "DELIVERY_SUCCESS",
    },
]


DEMO_POLICY_DECISION: PolicyDecision = {
    "decision": "PRIMARY",
    "selected_target": "endpoint-A",
    "reason": "Endpoint available",
}


DEMO_POLICY_EXPLANATION: PolicyExplanation = {
    "title": "Why was PRIMARY selected?",
    "reasons": [
        "Endpoint-A is AVAILABLE",
        "ERI: 100",
        "No active quarantine",
        "Primary route is currently preferred",
    ],
}


def create_eri_point(value: int) -> ERIPoint:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "value": value,
    }


def get_delivery_events() -> list[DeliveryEvent]:
    return list(DEMO_DELIVERY_EVENTS)


def get_policy_decision() -> PolicyDecision:
    return dict(DEMO_POLICY_DECISION)


def get_policy_explanation() -> PolicyExplanation:
    return {
        "title": DEMO_POLICY_EXPLANATION["title"],
        "reasons": list(DEMO_POLICY_EXPLANATION["reasons"]),
    }