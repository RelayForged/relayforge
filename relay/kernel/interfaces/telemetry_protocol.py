from typing import Protocol, Optional
from datetime import datetime


class TelemetryProtocol(Protocol):
    @property
    def attempt_id(self) -> str: ...
    
    @property
    def endpoint_id(self) -> str: ...
    
    @property
    def succeeded(self) -> bool: ...
    
    @property
    def http_status(self) -> Optional[int]: ...
    
    @property
    def latency_ms(self) -> float: ...
    
    @property
    def timed_out(self) -> bool: ...
    
    @property
    def is_http_5xx(self) -> bool: ...
    
    @property
    def observed_at(self) -> datetime: ...
