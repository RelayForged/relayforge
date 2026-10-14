"""Timed quarantine (ADR 0027): an expired hold is probed; failed probes back off."""
from datetime import datetime, timedelta, timezone

import pytest

from relay.control_plane.aede.service import AEDEService
from relay.control_plane.state_machine.quarantine import hold_expired, quarantine_hold_seconds
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.telemetry import Telemetry

T0 = datetime(2026, 10, 15, 2, 0, 0, tzinfo=timezone.utc)
EP = "ep-1"


@pytest.mark.parametrize(
    "relapses, expected", [(0, 300), (1, 600), (2, 1200), (3, 2400), (4, 3600), (9, 3600)]
)
def test_hold_doubles_after_each_relapse_up_to_the_cap(relapses, expected) -> None:
    assert quarantine_hold_seconds(300, 3600, relapses) == expected


def test_zero_base_disables_timed_quarantine() -> None:
    assert quarantine_hold_seconds(0, 3600, 0) == 0
    assert not hold_expired(T0, T0 + timedelta(days=1), 0)


def test_hold_expiry_boundary() -> None:
    assert not hold_expired(T0, T0 + timedelta(seconds=299), 300)
    assert hold_expired(T0, T0 + timedelta(seconds=300), 300)
    assert not hold_expired(None, T0, 300)


def failure(at: datetime, n: int) -> Telemetry:
    return Telemetry(
        attempt_id=f"fail-{n}-{at.timestamp()}",
        endpoint_id=EP,
        succeeded=False,
        http_status=None,
        latency_ms=1000.0,
        timed_out=True,
        observed_at=at,
    )


def success(at: datetime, n: int) -> Telemetry:
    return Telemetry(
        attempt_id=f"ok-{n}-{at.timestamp()}",
        endpoint_id=EP,
        succeeded=True,
        http_status=200,
        latency_ms=50.0,
        timed_out=False,
        observed_at=at,
    )


@pytest.fixture
def aede(monkeypatch):
    monkeypatch.setenv("AEDE_QUARANTINE_DURATION_SECONDS", "300")
    monkeypatch.setenv("AEDE_QUARANTINE_MAX_DURATION_SECONDS", "3600")
    monkeypatch.delenv("AEDE_RECOVERY_MIN_SAMPLES", raising=False)
    return AEDEService(publish_events=False)


async def quarantine(aede: AEDEService, start: datetime) -> datetime:
    at = start
    for n in range(30):
        at = start + timedelta(seconds=n)
        await aede.record_telemetry(failure(at, n))
        if aede.get_endpoint_state(EP) == EndpointState.QUARANTINED:
            return at
    raise AssertionError("endpoint never quarantined")


async def test_expired_hold_moves_to_recovering(aede) -> None:
    at = await quarantine(aede, T0)

    assert await aede.check_quarantine_hold(EP, at + timedelta(seconds=299)) == EndpointState.QUARANTINED
    assert await aede.check_quarantine_hold(EP, at + timedelta(seconds=300)) == EndpointState.RECOVERING
    assert "expired" in aede._explainability_repo.get_latest_by_endpoint(EP).reason


async def test_failed_probe_requarantines_for_twice_as_long(aede) -> None:
    at = await quarantine(aede, T0)
    probe_at = at + timedelta(seconds=300)
    await aede.check_quarantine_hold(EP, probe_at)

    requarantined_at = probe_at
    for n in range(10):
        requarantined_at = probe_at + timedelta(seconds=n + 1)
        await aede.record_telemetry(failure(requarantined_at, 100 + n))
        if aede.get_endpoint_state(EP) == EndpointState.QUARANTINED:
            break

    assert aede.get_endpoint_state(EP) == EndpointState.QUARANTINED
    assert aede.quarantine_hold(EP) == 600
    assert await aede.check_quarantine_hold(EP, requarantined_at + timedelta(seconds=599)) == EndpointState.QUARANTINED
    assert await aede.check_quarantine_hold(EP, requarantined_at + timedelta(seconds=600)) == EndpointState.RECOVERING


async def test_successful_recovery_resets_the_hold(aede) -> None:
    at = await quarantine(aede, T0)
    probe_at = at + timedelta(seconds=300)
    await aede.check_quarantine_hold(EP, probe_at)

    for n in range(5):
        await aede.record_telemetry(success(probe_at + timedelta(seconds=n + 1), n))

    assert aede.get_endpoint_state(EP) == EndpointState.AVAILABLE
    assert aede.quarantine_hold(EP) == 300


async def test_disabled_hold_waits_for_an_operator(monkeypatch) -> None:
    monkeypatch.setenv("AEDE_QUARANTINE_DURATION_SECONDS", "0")
    aede = AEDEService(publish_events=False)
    at = await quarantine(aede, T0)

    assert await aede.check_quarantine_hold(EP, at + timedelta(days=1)) == EndpointState.QUARANTINED


async def test_recovery_is_announced_to_the_listener(aede) -> None:
    import asyncio

    heard = []

    async def listener(endpoint_id, recovered_at):
        heard.append(endpoint_id)

    aede.set_recovery_listener(listener)
    at = await quarantine(aede, T0)
    probe_at = at + timedelta(seconds=300)
    await aede.check_quarantine_hold(EP, probe_at)
    for n in range(5):
        await aede.record_telemetry(success(probe_at + timedelta(seconds=n + 1), n))
    await asyncio.sleep(0)

    assert heard == [EP]


async def test_dispatcher_replays_an_endpoint_when_it_recovers(monkeypatch) -> None:
    from relay.data_plane.dispatcher import main as dispatcher_main

    monkeypatch.setenv("RELAY_AUTO_REPLAY", "true")
    requests = []

    class FakeReplayManager:
        def __init__(self, **kwargs):
            pass

        async def replay_dead_letters(self, *, now, endpoint_id, limit):
            requests.append((endpoint_id, limit))

            class Result:
                requested = 3

            return Result()

    monkeypatch.setattr(dispatcher_main, "ReplayManager", FakeReplayManager)
    aede = AEDEService(publish_events=False)
    dispatcher = dispatcher_main.Dispatcher(aede_service=aede)

    await aede._recovery_listener(EP, T0)

    assert requests == [(EP, 500)]
    assert dispatcher._auto_replay is True


def test_auto_replay_is_off_unless_configured(monkeypatch) -> None:
    from relay.data_plane.dispatcher.main import Dispatcher

    monkeypatch.delenv("RELAY_AUTO_REPLAY", raising=False)
    aede = AEDEService(publish_events=False)
    Dispatcher(aede_service=aede)

    assert aede._recovery_listener is None
