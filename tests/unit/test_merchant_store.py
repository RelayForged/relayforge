"""The Demo 2 merchant store: verifies, books each payment once, rejects forgeries."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from relay.infrastructure.auth.signing import compute_relay_signature

SECRET = "whsec-test"


@pytest.fixture
def store(monkeypatch, tmp_path):
    from examples.merchant_store import app as store_app

    monkeypatch.setattr(store_app, "SIGNING_SECRET", SECRET)
    monkeypatch.setattr(store_app, "DATA_FILE", tmp_path / "payments.jsonl")
    monkeypatch.setattr(store_app, "_rejected", 0)
    return TestClient(store_app.app)


def delivery(payment_id="pay_1", event_type="payment.captured", event_id="evt-1", secret=SECRET):
    body = json.dumps(
        {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": "2026-10-14T10:00:00+00:00",
            "payload": {"payment_id": payment_id, "amount": 499.0, "merchant_id": "acme"},
        }
    ).encode()
    timestamp = str(int(time.time()))
    headers = {
        "X-Relay-Signature": compute_relay_signature(secret, timestamp, "n-1", body),
        "X-Relay-Timestamp": timestamp,
        "X-Relay-Nonce": "n-1",
        "X-Relay-Attempt-Number": "1",
        "Content-Type": "application/json",
    }
    return body, headers


def test_books_a_verified_payment(store):
    body, headers = delivery()

    assert store.post("/webhook", content=body, headers=headers).json() == {"status": "ok"}

    payments = store.get("/payments").json()["payments"]
    assert [(p["payment_id"], p["amount"], p["verified"]) for p in payments] == [("pay_1", 499.0, True)]


def test_redelivery_and_replay_are_booked_once(store):
    body, headers = delivery(event_id="evt-1")
    replay_body, replay_headers = delivery(event_id="evt-replay")  # same payment, new event id

    store.post("/webhook", content=body, headers=headers)
    store.post("/webhook", content=body, headers=headers)
    second = store.post("/webhook", content=replay_body, headers=replay_headers)

    assert second.json() == {"status": "duplicate"}
    assert len(store.get("/payments").json()["payments"]) == 1


def test_captured_and_failed_are_separate_facts(store):
    for event_type in ("payment.failed", "payment.captured"):
        body, headers = delivery(event_type=event_type)
        store.post("/webhook", content=body, headers=headers)

    assert len(store.get("/payments").json()["payments"]) == 2


def test_forged_request_is_rejected_and_counted(store):
    body, headers = delivery(secret="not-the-secret")

    response = store.post("/webhook", content=body, headers=headers)

    assert response.status_code == 401
    inbox = store.get("/payments").json()
    assert inbox["payments"] == []
    assert inbox["rejected_signatures"] == 1


def test_inbox_page_renders(store):
    page = store.get("/")

    assert page.status_code == 200
    assert "Payment notifications from PayFlow" in page.text
