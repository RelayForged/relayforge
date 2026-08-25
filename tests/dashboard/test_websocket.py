import pytest

from relay.dashboard.demo_source import DashboardDemoSource


def test_demo_source_configure_updates_failure_rate() -> None:
    source = DashboardDemoSource()

    source.configure(failure_rate=1.0)

    assert source.failure_rate == 1.0


def test_demo_source_configure_updates_latency() -> None:
    source = DashboardDemoSource()

    source.configure(latency_ms=2500.0)

    assert source.latency_ms == 2500.0


def test_demo_source_configure_updates_failure_mode() -> None:
    source = DashboardDemoSource()

    source.configure(failure_mode="timeout")

    assert source.failure_mode == "timeout"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"failure_rate": -0.1},
        {"failure_rate": 1.1},
        {"latency_ms": 0},
        {"latency_ms": -1},
        {"failure_mode": "invalid"},
    ],
)
def test_demo_source_configure_rejects_invalid_values(
    kwargs: dict,
) -> None:
    source = DashboardDemoSource()

    with pytest.raises(ValueError):
        source.configure(**kwargs)