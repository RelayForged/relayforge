from typing import TypedDict


class ERIPoint(TypedDict):
    timestamp: str
    value: int


class StateUpdate(TypedDict):
    type: str
    state: str
    eri: int
    eri_history: list[ERIPoint]