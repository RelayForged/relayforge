from collections import deque
from typing import Any, Deque, Dict

from simulator.config import SimulatorConfig

RECEIVED_HISTORY_SIZE = 200


class SimulatorState:
    def __init__(self) -> None:
        self.config = SimulatorConfig()
        # What the receiver saw, newest last. Lets the dashboard show
        # receiver-side signature verification per delivery attempt.
        self.received: Deque[Dict[str, Any]] = deque(maxlen=RECEIVED_HISTORY_SIZE)
