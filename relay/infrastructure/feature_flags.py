"""
Feature Flags System for Research Switches.

Controls runtime behavior without redeployment. Used during experimental
benchmarking to toggle delivery strategies and individual research components.

Flags:
    AEDE_ENABLED:              Master switch for AEDE vs static fallback.
    ERI_ENABLED:               Enables composite ERI score evaluation.
    STATE_MACHINE_ENABLED:     Enables 5-state Endpoint State Machine vs binary.
    STATIC_RETRY_MODE:         Forces Strategy A (static exponential backoff).
    ADAPTIVE_RETRY_MODE:       Forces Strategy B (adaptive backoff without state).

See docs/architecture.md § Feature Flags for Research.
"""

import os
from pydantic import BaseModel, ConfigDict, Field


class FeatureFlags(BaseModel):
    """
    Immutable feature flags snapshot.
    Loaded from environment variables or platform configuration.
    """

    model_config = ConfigDict(frozen=True)

    aede_enabled: bool = Field(
        default=True,
        description="Master switch for AEDE policy engine.",
    )
    eri_enabled: bool = Field(
        default=True,
        description="Enables composite ERI computation.",
    )
    state_machine_enabled: bool = Field(
        default=True,
        description="Enables full 5-state Endpoint State Machine.",
    )
    static_retry_mode: bool = Field(
        default=False,
        description="Forces Strategy A (static exponential backoff).",
    )
    adaptive_retry_mode: bool = Field(
        default=False,
        description="Forces Strategy B (adaptive backoff without state machine).",
    )

    @property
    def active_strategy_name(self) -> str:
        """
        Derives the active delivery strategy name based on flag combinations.
        """
        if self.static_retry_mode or not self.aede_enabled:
            return "STRATEGY_A"
        elif self.adaptive_retry_mode or not self.state_machine_enabled:
            return "STRATEGY_B"
        else:
            return "STRATEGY_C"

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        """
        Loads feature flags from environment variables with sensible defaults.
        """
        return cls(
            aede_enabled=os.getenv("AEDE_ENABLED", "true").lower() in ("true", "1", "yes"),
            eri_enabled=os.getenv("ERI_ENABLED", "true").lower() in ("true", "1", "yes"),
            state_machine_enabled=os.getenv("STATE_MACHINE_ENABLED", "true").lower() in ("true", "1", "yes"),
            static_retry_mode=os.getenv("STATIC_RETRY_MODE", "false").lower() in ("true", "1", "yes"),
            adaptive_retry_mode=os.getenv("ADAPTIVE_RETRY_MODE", "false").lower() in ("true", "1", "yes"),
        )
