from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore
from relay.kernel.value_objects.endpoint_state import StateMachineThresholds


def next_state(
    current: EndpointState,
    eri: ERIScore,
    thresholds: StateMachineThresholds,
    manual_quarantine: bool = False,
) -> EndpointState:
    if manual_quarantine:
        return EndpointState.QUARANTINED

    score = eri.score

    if current == EndpointState.AVAILABLE:
        if score < thresholds.degradation_threshold:
            if score < thresholds.quarantine_threshold:
                return EndpointState.QUARANTINED
            if score < thresholds.unstable_threshold:
                return EndpointState.UNSTABLE
            return EndpointState.OBSERVED_DEGRADATION
        return EndpointState.AVAILABLE

    elif current == EndpointState.OBSERVED_DEGRADATION:
        if score > thresholds.degradation_recovery_threshold:
            return EndpointState.AVAILABLE
        if score < thresholds.quarantine_threshold:
            return EndpointState.QUARANTINED
        if score < thresholds.unstable_threshold:
            return EndpointState.UNSTABLE
        return EndpointState.OBSERVED_DEGRADATION

    elif current == EndpointState.UNSTABLE:
        if score < thresholds.quarantine_threshold:
            return EndpointState.QUARANTINED
        if score > thresholds.unstable_recovery_threshold:
            return EndpointState.OBSERVED_DEGRADATION
        return EndpointState.UNSTABLE

    elif current == EndpointState.QUARANTINED:
        return EndpointState.QUARANTINED

    elif current == EndpointState.RECOVERING:
        if score < thresholds.quarantine_threshold:
            return EndpointState.QUARANTINED
        # After an operator restore the window starts empty (ADR 0020), and
        # one good probe scores ~100. The minimum sample count makes recovery
        # rest on more than that one probe (ADR 0023).
        if (
            score > thresholds.recovery_threshold
            and eri.window_size >= thresholds.recovery_min_samples
        ):
            return EndpointState.AVAILABLE
        return EndpointState.RECOVERING

    return current
