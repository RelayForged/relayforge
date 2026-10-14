from pydantic import BaseModel, ConfigDict, Field, model_validator
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
    platform/control_plane/state_machine/. All policy derivation lives in
    platform/control_plane/aede/. This is the vocabulary, not the grammar.
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




class StateMachineThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    degradation_threshold: float = Field(ge=0.0, le=100.0)
    degradation_recovery_threshold: float = Field(ge=0.0, le=100.0)
    unstable_threshold: float = Field(ge=0.0, le=100.0)
    unstable_recovery_threshold: float = Field(ge=0.0, le=100.0)
    quarantine_threshold: float = Field(ge=0.0, le=100.0)
    recovery_threshold: float = Field(ge=0.0, le=100.0)
    # Probation (ADR 0023): RECOVERING promotes to AVAILABLE only once the
    # ERI window holds at least this many observations. 0 disables it, which
    # is the benchmark's model.
    recovery_min_samples: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_hysteresis(self) -> "StateMachineThresholds":
        if self.degradation_recovery_threshold <= self.degradation_threshold:
            raise ValueError("Hysteresis violation: degradation_recovery_threshold must be strictly greater than degradation_threshold.")
        if self.unstable_recovery_threshold <= self.unstable_threshold:
            raise ValueError("Hysteresis violation: unstable_recovery_threshold must be strictly greater than unstable_threshold.")
        return self

    @classmethod
    def from_config(cls, config_dict: dict) -> "StateMachineThresholds":
        return cls(**config_dict)
