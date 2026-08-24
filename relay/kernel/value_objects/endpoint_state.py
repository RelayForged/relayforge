from enum import Enum


class EndpointState(str, Enum):
    """
    The five operational states an endpoint can occupy within the Endpoint State Machine.

    State transitions are driven exclusively by the Endpoint Reliability Index (ERI).
    No single failure triggers a transition — transitions require sustained evidence
    accumulated across the ERI observation window (ADR 0008).

    Inherits from str so that the enum value serialises cleanly to JSON without
    needing a custom encoder (e.g. "AVAILABLE" rather than <EndpointState.AVAILABLE>).

    This enum owns nothing beyond the named states. All transition logic lives in
    relay/control_plane/state_machine/. All policy derivation lives in
    relay/control_plane/aede/. This is the vocabulary, not the grammar.
    """

    AVAILABLE = "AVAILABLE"
    """
    Endpoint is operating normally.
    High success rate, low latency, stable behaviour.
    Policy: immediate delivery, normal concurrency, standard monitoring frequency.
    """

    OBSERVED_DEGRADATION = "OBSERVED_DEGRADATION"
    """
    Early warning state. Endpoint has begun exhibiting abnormal behaviour but
    remains operational (increasing latency, isolated failures, elevated timeouts).
    Policy: increased monitoring frequency, moderate retry backoff, reduced parallel delivery.
    """

    UNSTABLE = "UNSTABLE"
    """
    Persistent instability. Repeated failures, poor success rate, sustained latency increase.
    Policy: aggressive backoff, suspend parallel deliveries, queue future events,
    notify administrator.
    """

    QUARANTINED = "QUARANTINED"
    """
    Delivery intentionally suspended. Endpoint considered unavailable.
    Policy: stop all delivery, preserve all queued events, await recovery evidence,
    permit manual replay only.
    """

    RECOVERING = "RECOVERING"
    """
    Endpoint has shown evidence of recovery but stability is not yet confirmed.
    Policy: gradual traffic restoration (single-stream only), conservative retry
    intervals, intensive monitoring.
    Successful sustained recovery transitions back to AVAILABLE.
    """
