from datetime import datetime, timezone

from relay.dashboard.adapter import get_delivery_event
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt


def test_get_delivery_event_maps_successful_attempt() -> None:
    delivery = Delivery(
        delivery_id="delivery-1",
        event_id="event-1",
        subscription_id="subscription-1",
        endpoint_id="endpoint-1",
        status="SUCCESS",
    )

    started_at = datetime(
        2026,
        8,
        25,
        10,
        0,
        tzinfo=timezone.utc,
    )

    attempt = DeliveryAttempt(
        attempt_id="attempt-1",
        delivery_id="delivery-1",
        attempt_number=1,
        started_at=started_at,
        http_status=200,
        success=True,
    )

    event = get_delivery_event(delivery, attempt)

    assert event["id"] == "attempt-1"
    assert event["type"] == "DELIVERY_SUCCESS"
    assert event["delivery_id"] == "delivery-1"
    assert event["event_id"] == "event-1"
    assert event["attempt_id"] == "attempt-1"
    assert event["attempt_number"] == 1
    assert event["http_status"] == 200
    assert event["success"] is True
    assert event["status"] == "SUCCESS"
    assert event["error_message"] is None


def test_get_delivery_event_maps_failed_attempt() -> None:
    delivery = Delivery(
        delivery_id="delivery-2",
        event_id="event-2",
        subscription_id="subscription-2",
        endpoint_id="endpoint-2",
        status="FAILED",
    )

    started_at = datetime(
        2026,
        8,
        25,
        10,
        1,
        tzinfo=timezone.utc,
    )

    attempt = DeliveryAttempt(
        attempt_id="attempt-2",
        delivery_id="delivery-2",
        attempt_number=2,
        started_at=started_at,
        http_status=500,
        success=False,
        error_message="Endpoint returned HTTP 500",
    )

    event = get_delivery_event(delivery, attempt)

    assert event["id"] == "attempt-2"
    assert event["type"] == "DELIVERY_FAILURE"
    assert event["delivery_id"] == "delivery-2"
    assert event["attempt_number"] == 2
    assert event["http_status"] == 500
    assert event["success"] is False
    assert event["status"] == "FAILED"
    assert event["error_message"] == "Endpoint returned HTTP 500"