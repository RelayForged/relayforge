"""Fixtures for the Person A data-plane suite. Doubles live in _helpers.py."""
from __future__ import annotations

import pytest

from tests.data_plane._helpers import (
    FakeAttemptRepository,
    FakeConsumer,
    FakeDeliveryRepository,
    FakeTelemetryRepository,
)


@pytest.fixture
def deliveries() -> FakeDeliveryRepository:
    return FakeDeliveryRepository()


@pytest.fixture
def attempts() -> FakeAttemptRepository:
    return FakeAttemptRepository()


@pytest.fixture
def telemetry() -> FakeTelemetryRepository:
    return FakeTelemetryRepository()


@pytest.fixture
def consumer() -> FakeConsumer:
    return FakeConsumer()


@pytest.fixture
def captured_publishes(monkeypatch) -> list[tuple[str, str, bytes]]:
    """
    Intercept the Kafka producer the Retry Manager publishes through.
    Patched at the point of use, since manager.py imports `publish` by
    name.
    """
    published: list[tuple[str, str, bytes]] = []

    async def fake_publish(topic: str, key: str, value: bytes) -> None:
        published.append((topic, key, value))

    monkeypatch.setattr("relay.data_plane.retry_manager.manager.publish", fake_publish)
    return published


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """
    The egress guard and HMAC signer read env vars at call time, and
    scripts/week34_smoke.py sets RELAY_ALLOW_PRIVATE_TARGETS. Force the
    secure defaults so a stray value in the developer's shell cannot mask
    a regression in either control.
    """
    monkeypatch.delenv("RELAY_ALLOW_PRIVATE_TARGETS", raising=False)
    monkeypatch.delenv("RELAY_WEBHOOK_SIGNING_SECRET", raising=False)
