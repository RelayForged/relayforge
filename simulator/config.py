from enum import Enum

from pydantic import BaseModel, Field


class FailureMode(str, Enum):
    SUCCESS = "success"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    SLOW = "slow"
    RATE_LIMIT = "rate_limit"
    MIXED = "mixed"
    AUTH_FAILURE = "auth_failure"
    CONNECTION_RESET = "connection_reset"


class SimulatorConfig(BaseModel):
    mode: FailureMode = FailureMode.HTTP_ERROR
    rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0)
