import pytest

from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore
from relay.kernel.value_objects.endpoint_state import StateMachineThresholds
from relay.control_plane.state_machine.transitions import next_state


@pytest.fixture
def thresholds():
    return StateMachineThresholds(
        degradation_threshold=80.0,
        degradation_recovery_threshold=90.0,
        unstable_threshold=60.0,
        unstable_recovery_threshold=70.0,
        quarantine_threshold=30.0,
        recovery_threshold=90.0,
    )


def test_hysteresis(thresholds):
    # Oscillating ERI around the degradation boundary
    eri_values = [82.0, 78.0, 84.0, 79.0, 83.0, 77.0]
    current = EndpointState.AVAILABLE
    
    history = []
    for score in eri_values:
        eri = ERIScore.perfect(window_size=10)
        # Hack to bypass frozen for test - normally we'd create it with the score
        object.__setattr__(eri, 'score', score)
        
        current = next_state(current, eri, thresholds)
        history.append(current)

    assert history == [
        EndpointState.AVAILABLE,
        EndpointState.OBSERVED_DEGRADATION,
        EndpointState.OBSERVED_DEGRADATION,
        EndpointState.OBSERVED_DEGRADATION,
        EndpointState.OBSERVED_DEGRADATION,
        EndpointState.OBSERVED_DEGRADATION,
    ]


def test_non_adjacent_transitions(thresholds):
    eri = ERIScore.perfect(window_size=10)
    
    # 1. OBSERVED_DEGRADATION -> QUARANTINED (collapse)
    object.__setattr__(eri, 'score', 20.0)
    assert next_state(EndpointState.OBSERVED_DEGRADATION, eri, thresholds) == EndpointState.QUARANTINED
    
    # 2. RECOVERING -> QUARANTINED (collapse during recovery)
    assert next_state(EndpointState.RECOVERING, eri, thresholds) == EndpointState.QUARANTINED
    
    # 3. ANY -> QUARANTINED (manual)
    object.__setattr__(eri, 'score', 95.0)
    assert next_state(EndpointState.AVAILABLE, eri, thresholds, manual_quarantine=True) == EndpointState.QUARANTINED
    
    # 4. AVAILABLE -> UNSTABLE (sudden collapse)
    object.__setattr__(eri, 'score', 50.0)
    assert next_state(EndpointState.AVAILABLE, eri, thresholds) == EndpointState.UNSTABLE


def test_determinism(thresholds):
    eri = ERIScore.perfect(window_size=10)
    object.__setattr__(eri, 'score', 75.0)
    
    run_1 = next_state(EndpointState.AVAILABLE, eri, thresholds)
    run_2 = next_state(EndpointState.AVAILABLE, eri, thresholds)
    run_3 = next_state(EndpointState.AVAILABLE, eri, thresholds)
    
    assert run_1 == run_2 == run_3 == EndpointState.OBSERVED_DEGRADATION


def test_recovery_conservatism():
    # Attempt to create thresholds where recovery <= degradation
    with pytest.raises(ValueError, match="Hysteresis violation"):
        StateMachineThresholds(
            degradation_threshold=80.0,
            degradation_recovery_threshold=80.0,
            unstable_threshold=60.0,
            unstable_recovery_threshold=70.0,
            quarantine_threshold=30.0,
            recovery_threshold=90.0,
        )
