"""Failure scenario definitions for benchmark experiments.

Each scenario is a sequence of ScenarioPhase objects. A phase defines the
telemetry profile (success rate, latency, timeouts, 5xx errors) for a block
of delivery attempts. The BenchmarkRunner iterates through phases in order,
generating synthetic Telemetry records consumed by the ERI calculator.

These represent the 10 failure scenarios described in docs/research.md and docs/execution-plan.md.
"""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ScenarioPhase:
    """An individual phase within a failure injection scenario."""
    duration_events: int
    success_rate: float  # 0.0 to 1.0
    latency_ms: float
    timed_out_rate: float  # fraction of events that timed out
    http_5xx_rate: float  # fraction of events returning HTTP 5xx
    description: str


@dataclass(frozen=True)
class Scenario:
    """A benchmark failure scenario composed of ordered fault injection phases."""
    scenario_id: int
    name: str
    description: str
    phases: List[ScenarioPhase]


SCENARIO_1_INTERMITTENT_TIMEOUTS = Scenario(
    scenario_id=1,
    name="Scenario 1: Intermittent Timeouts",
    description="Random request timeouts mixed with healthy traffic.",
    phases=[
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Nominal operation"),
        ScenarioPhase(duration_events=100, success_rate=0.7, latency_ms=450.0, timed_out_rate=0.3, http_5xx_rate=0.0, description="30% intermittent timeouts"),
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Post-fault recovery"),
    ],
)

SCENARIO_2_HIGH_LATENCY = Scenario(
    scenario_id=2,
    name="Scenario 2: High Latency",
    description="Gradually increasing response latency with intact success rate.",
    phases=[
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Low baseline latency"),
        ScenarioPhase(duration_events=60, success_rate=1.0, latency_ms=1500.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Moderate latency increase"),
        ScenarioPhase(duration_events=80, success_rate=1.0, latency_ms=4500.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Severe latency bottleneck"),
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=120.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Latency returns to baseline"),
    ],
)

SCENARIO_3_BURST_FAILURES = Scenario(
    scenario_id=3,
    name="Scenario 3: Burst Failures",
    description="Sudden 100% failure rate for a short window, then immediate recovery.",
    phases=[
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=80.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Nominal traffic"),
        ScenarioPhase(duration_events=60, success_rate=0.0, latency_ms=500.0, timed_out_rate=0.0, http_5xx_rate=1.0, description="100% 500 error burst"),
        ScenarioPhase(duration_events=80, success_rate=1.0, latency_ms=80.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Immediate recovery"),
    ],
)

SCENARIO_4_ENDPOINT_OUTAGE = Scenario(
    scenario_id=4,
    name="Scenario 4: Endpoint Outage",
    description="Extended unavailability triggering quarantine isolation.",
    phases=[
        ScenarioPhase(duration_events=40, success_rate=1.0, latency_ms=90.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Nominal traffic"),
        ScenarioPhase(duration_events=150, success_rate=0.0, latency_ms=5000.0, timed_out_rate=0.8, http_5xx_rate=0.2, description="Complete endpoint outage"),
    ],
)

SCENARIO_5_PARTIAL_RECOVERY = Scenario(
    scenario_id=5,
    name="Scenario 5: Partial Recovery",
    description="Unstable endpoint fluctuating around recovery thresholds.",
    phases=[
        ScenarioPhase(duration_events=40, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Initial nominal baseline"),
        ScenarioPhase(duration_events=60, success_rate=0.1, latency_ms=3000.0, timed_out_rate=0.5, http_5xx_rate=0.4, description="Severe failure"),
        ScenarioPhase(duration_events=100, success_rate=0.55, latency_ms=1200.0, timed_out_rate=0.2, http_5xx_rate=0.25, description="Flapping / partial recovery"),
    ],
)

SCENARIO_6_CREDENTIAL_FAILURE = Scenario(
    scenario_id=6,
    name="Scenario 6: Credential Failure",
    description="Authentication/credential expiration returning HTTP 401/403.",
    phases=[
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Valid credentials"),
        ScenarioPhase(duration_events=80, success_rate=0.0, latency_ms=150.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="401 Unauthorized rejection"),
        ScenarioPhase(duration_events=60, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Credentials rotated and restored"),
    ],
)

SCENARIO_7_CONNECTION_FAILURE = Scenario(
    scenario_id=7,
    name="Scenario 7: Connection Failure",
    description="Abrupt TCP resets and TLS handshake failures.",
    phases=[
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=90.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Nominal connection"),
        ScenarioPhase(duration_events=80, success_rate=0.0, latency_ms=50.0, timed_out_rate=0.9, http_5xx_rate=0.0, description="Connection reset drops"),
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=90.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Stable reconnection"),
    ],
)

SCENARIO_8_RATE_LIMITING = Scenario(
    scenario_id=8,
    name="Scenario 8: Rate Limiting",
    description="Endpoint applies throttling returning HTTP 429 Too Many Requests.",
    phases=[
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=110.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Under rate limit"),
        ScenarioPhase(duration_events=100, success_rate=0.4, latency_ms=250.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="429 Rate limited rejects"),
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=110.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Rate limit bucket replenished"),
    ],
)

SCENARIO_9_NETWORK_PARTITION = Scenario(
    scenario_id=9,
    name="Scenario 9: Network Partition",
    description="Complete route unreachable resulting in max network timeouts.",
    phases=[
        ScenarioPhase(duration_events=40, success_rate=1.0, latency_ms=80.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Network online"),
        ScenarioPhase(duration_events=100, success_rate=0.0, latency_ms=5000.0, timed_out_rate=1.0, http_5xx_rate=0.0, description="Network partition unreachable"),
        ScenarioPhase(duration_events=50, success_rate=1.0, latency_ms=80.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Route restored"),
    ],
)

SCENARIO_10_SLOW_DEGRADATION = Scenario(
    scenario_id=10,
    name="Scenario 10: Slow Degradation",
    description="Gradual step-down from 100% to 0% success — showcases AEDE adaptive state isolation advantage.",
    phases=[
        ScenarioPhase(duration_events=40, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Nominal 100%"),
        ScenarioPhase(duration_events=40, success_rate=0.85, latency_ms=350.0, timed_out_rate=0.05, http_5xx_rate=0.1, description="Minor degradation (85%)"),
        ScenarioPhase(duration_events=40, success_rate=0.60, latency_ms=800.0, timed_out_rate=0.15, http_5xx_rate=0.25, description="Moderate degradation (60%)"),
        ScenarioPhase(duration_events=40, success_rate=0.35, latency_ms=2000.0, timed_out_rate=0.30, http_5xx_rate=0.35, description="Severe instability (35%)"),
        ScenarioPhase(duration_events=40, success_rate=0.0, latency_ms=4500.0, timed_out_rate=0.50, http_5xx_rate=0.50, description="Complete failure (0%)"),
        ScenarioPhase(duration_events=60, success_rate=1.0, latency_ms=100.0, timed_out_rate=0.0, http_5xx_rate=0.0, description="Full recovery (100%)"),
    ],
)

ALL_SCENARIOS = [
    SCENARIO_1_INTERMITTENT_TIMEOUTS,
    SCENARIO_2_HIGH_LATENCY,
    SCENARIO_3_BURST_FAILURES,
    SCENARIO_4_ENDPOINT_OUTAGE,
    SCENARIO_5_PARTIAL_RECOVERY,
    SCENARIO_6_CREDENTIAL_FAILURE,
    SCENARIO_7_CONNECTION_FAILURE,
    SCENARIO_8_RATE_LIMITING,
    SCENARIO_9_NETWORK_PARTITION,
    SCENARIO_10_SLOW_DEGRADATION,
]


def get_all_scenarios() -> List[Scenario]:
    """Return all 10 defined research benchmark scenarios."""
    return list(ALL_SCENARIOS)


def get_scenario(scenario_id: int) -> Scenario:
    """Retrieve a specific benchmark scenario by ID (1 to 10)."""
    for sc in ALL_SCENARIOS:
        if sc.scenario_id == scenario_id:
            return sc
    raise ValueError(f"Scenario ID {scenario_id} not found. Must be between 1 and 10.")
