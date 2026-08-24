import yaml
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict
from relay.kernel.value_objects.eri import ERIWeights
from relay.kernel.value_objects.endpoint_state import StateMachineThresholds


class AdaptivePolicyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    quarantine_duration_seconds: int
    replay_batch_size: int
    recovery_parallelism: int


class PlatformConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    eri: ERIWeights
    state_machine: StateMachineThresholds
    adaptive_policy: AdaptivePolicyConfig
    
    window_size: int
    baseline_latency_ms: float
    max_latency_ms: float
    max_consecutive_failures: int
    max_queue_depth: int
    recovery_window_size: int


class ConfigLoader:
    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> PlatformConfig:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path.absolute()}")

        with open(config_path, "r", encoding="utf-8") as f:
            raw_config: dict[str, Any] = yaml.safe_load(f)

        eri_config = raw_config.get("eri", {})
        sm_config = raw_config.get("state_machine", {})
        ap_config = raw_config.get("adaptive_policy", {})

        return PlatformConfig(
            eri=ERIWeights.from_config(eri_config),
            state_machine=StateMachineThresholds.from_config(sm_config),
            adaptive_policy=AdaptivePolicyConfig(**ap_config),
            window_size=eri_config["window_size"],
            baseline_latency_ms=eri_config["baseline_latency_ms"],
            max_latency_ms=eri_config["max_latency_ms"],
            max_consecutive_failures=eri_config["max_consecutive_failures"],
            max_queue_depth=eri_config["max_queue_depth"],
            recovery_window_size=sm_config["recovery_window_size"],
        )
