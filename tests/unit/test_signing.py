"""HMAC signing: shared helpers, policy wiring, and receiver-side verification."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from relay.control_plane.aede.service import AEDEService
from relay.control_plane.aede.strategies import StrategyA, StrategyB, StrategyC
from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.infrastructure.auth import (
    compute_relay_signature,
    hmac_required_from_env,
    verify_relay_signature,
)
from relay.infrastructure.cache import InMemoryPolicyCache, PolicyFallbackManager
from relay.kernel.value_objects.endpoint_state import (
    EndpointState,
    StateMachineThresholds,
)
from relay.kernel.value_objects.eri import ERIScore
from relay.kernel.value_objects.telemetry import Telemetry

NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)
TS = str(int(NOW.timestamp()))
SECRET = "s3cret"
BODY = b'{"order_id":"42"}'

THRESHOLDS = StateMachineThresholds(
    degradation_threshold=75.0,
    degradation_recovery_threshold=95.0,
    unstable_threshold=55.0,
    unstable_recovery_threshold=85.0,
    quarantine_threshold=30.0,
    recovery_threshold=80.0,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def test_sign_then_verify_round_trip() -> None:
    signature = compute_relay_signature(SECRET, TS, "nonce-1", BODY)

    assert signature.startswith("sha256=")
    assert verify_relay_signature(
        SECRET, timestamp=TS, nonce="nonce-1", body=BODY, signature=signature, now=NOW
    )


@pytest.mark.parametrize(
    "override",
    [
        {"body": b'{"order_id":"43"}'},
        {"nonce": "nonce-2"},
        {"timestamp": str(int(NOW.timestamp()) + 1)},
        {"secret": "other"},
        {"signature": "sha256=" + "0" * 64},
        {"signature": ""},
    ],
)
def test_any_tampering_fails_verification(override) -> None:
    args = {
        "secret": SECRET,
        "timestamp": TS,
        "nonce": "nonce-1",
        "body": BODY,
        "signature": compute_relay_signature(SECRET, TS, "nonce-1", BODY),
    }
    args.update(override)
    secret = args.pop("secret")

    assert not verify_relay_signature(secret, now=NOW, **args)


def test_stale_signature_is_rejected_unless_freshness_is_disabled() -> None:
    signature = compute_relay_signature(SECRET, TS, "", BODY)
    later = NOW + timedelta(seconds=301)
    kwargs = dict(timestamp=TS, nonce="", body=BODY, signature=signature)

    assert verify_relay_signature(SECRET, now=NOW + timedelta(seconds=299), **kwargs)
    assert not verify_relay_signature(SECRET, now=later, **kwargs)
    assert verify_relay_signature(SECRET, now=later, max_age_seconds=None, **kwargs)


def test_non_numeric_timestamp_is_rejected() -> None:
    signature = compute_relay_signature(SECRET, "soon", "", BODY)

    assert not verify_relay_signature(
        SECRET, timestamp="soon", nonce="", body=BODY, signature=signature, now=NOW
    )


@pytest.mark.parametrize(
    "value, expected", [("true", True), ("1", True), ("false", False), (None, False)]
)
def test_hmac_required_from_env(monkeypatch, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("RELAY_HMAC_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("RELAY_HMAC_REQUIRED", value)

    assert hmac_required_from_env() is expected


# --------------------------------------------------------------------------
# Policy wiring: an explicit input, identical for every strategy
# --------------------------------------------------------------------------


def _synthesize(hmac_required=None):
    kwargs = {} if hmac_required is None else {"hmac_required": hmac_required}
    policy, _ = synthesize_policy(
        endpoint_id="ep-1",
        state_before=EndpointState.AVAILABLE,
        state_after=EndpointState.AVAILABLE,
        eri=ERIScore.perfect(window_size=10, computed_at=NOW),
        thresholds=THRESHOLDS,
        computed_at=NOW,
        **kwargs,
    )
    return policy


def test_synthesizer_defaults_to_unsigned() -> None:
    assert _synthesize().integrity_policy.hmac_required is False


def test_synthesizer_signs_when_told_to() -> None:
    integrity = _synthesize(hmac_required=True).integrity_policy

    assert integrity.hmac_required is True
    assert integrity.replay_protection is True
    assert integrity.signature_ttl_seconds == 300


def test_synthesizer_ignores_the_environment(monkeypatch) -> None:
    """The flag is an argument; an env var must not change the policy."""
    monkeypatch.setenv("RELAY_HMAC_REQUIRED", "true")

    assert _synthesize().integrity_policy.hmac_required is False


@pytest.mark.parametrize("strategy_cls", [StrategyA, StrategyB, StrategyC])
@pytest.mark.parametrize("required", [True, False])
def test_every_strategy_carries_the_flag(strategy_cls, required) -> None:
    policy, _ = strategy_cls(hmac_required=required).evaluate(
        endpoint_id="ep-1",
        endpoint_state=EndpointState.AVAILABLE,
        eri=ERIScore.perfect(window_size=10, computed_at=NOW),
        computed_at=NOW,
    )

    assert policy.integrity_policy.hmac_required is required


def test_tier3_fallback_keeps_signing() -> None:
    manager = PolicyFallbackManager(cache=InMemoryPolicyCache(), hmac_required=True)

    policy = manager.get_tier_3_static_policy("ep-1", computed_at=NOW)

    assert policy.integrity_policy.hmac_required is True


async def test_aede_service_reads_the_flag_once_at_construction(monkeypatch) -> None:
    monkeypatch.setenv("RELAY_HMAC_REQUIRED", "true")
    service = AEDEService(publish_events=False)
    monkeypatch.setenv("RELAY_HMAC_REQUIRED", "false")

    _, _, policy, _ = await service.record_telemetry(
        Telemetry(
            attempt_id="a-1",
            endpoint_id="ep-1",
            succeeded=True,
            http_status=200,
            latency_ms=50.0,
            timed_out=False,
            observed_at=NOW,
        )
    )

    assert policy.integrity_policy.hmac_required is True
    assert service.get_current_policy("ep-1").integrity_policy.hmac_required is True


# --------------------------------------------------------------------------
# Receiver: the simulator verifies what the worker signs
# --------------------------------------------------------------------------


@pytest.fixture
def simulator(monkeypatch):
    from simulator.config import FailureMode, SimulatorConfig
    from simulator.main import app, simulator_state

    monkeypatch.setenv("RELAY_WEBHOOK_SIGNING_SECRET", SECRET)
    previous = simulator_state.config
    simulator_state.config = SimulatorConfig(mode=FailureMode.SUCCESS)
    simulator_state.received.clear()
    yield TestClient(app)
    simulator_state.config = previous
    simulator_state.received.clear()


def _signed_headers(body: bytes, *, secret: str = SECRET, attempt_id: str = "attempt-1"):
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    return {
        "X-Relay-Attempt-Id": attempt_id,
        "X-Relay-Event-Id": "event-1",
        "X-Relay-Timestamp": timestamp,
        "X-Relay-Nonce": "nonce-1",
        "X-Relay-Signature": compute_relay_signature(secret, timestamp, "nonce-1", body),
    }


def test_simulator_accepts_and_records_a_valid_signature(simulator) -> None:
    res = simulator.post("/webhook", content=BODY, headers=_signed_headers(BODY))

    assert res.status_code == 200
    seen = simulator.get("/simulator/deliveries").json()[0]
    assert seen["attempt_id"] == "attempt-1"
    assert seen["event_id"] == "event-1"
    assert seen["signature_status"] == "verified"
    assert seen["response_status"] == 200
    assert seen["signature"].startswith("sha256=")
    assert len(seen["signature"]) < len(_signed_headers(BODY)["X-Relay-Signature"])


def test_simulator_rejects_a_forged_signature(simulator) -> None:
    res = simulator.post(
        "/webhook", content=BODY, headers=_signed_headers(BODY, secret="wrong")
    )

    assert res.status_code == 401
    assert simulator.get("/simulator/deliveries").json()[0]["signature_status"] == "invalid"


def test_simulator_rejects_a_body_changed_after_signing(simulator) -> None:
    res = simulator.post("/webhook", content=b'{"order_id":"43"}', headers=_signed_headers(BODY))

    assert res.status_code == 401


def test_simulator_marks_unsigned_requests_and_still_serves_them(simulator) -> None:
    res = simulator.post("/webhook", content=BODY)

    assert res.status_code == 200
    seen = simulator.get("/simulator/deliveries").json()[0]
    assert seen["signature_status"] == "unsigned"
    assert seen["signature"] is None


def test_simulator_cannot_verify_without_a_secret(simulator, monkeypatch) -> None:
    monkeypatch.delenv("RELAY_WEBHOOK_SIGNING_SECRET")

    res = simulator.post("/webhook", content=BODY, headers=_signed_headers(BODY))

    assert res.status_code == 200
    assert simulator.get("/simulator/deliveries").json()[0]["signature_status"] == "unverifiable"


def test_simulator_deliveries_are_newest_first_and_limited(simulator) -> None:
    for i in range(5):
        simulator.post("/webhook", content=BODY, headers={"X-Relay-Attempt-Id": f"a-{i}"})

    seen = simulator.get("/simulator/deliveries", params={"limit": 2}).json()

    assert [s["attempt_id"] for s in seen] == ["a-4", "a-3"]
