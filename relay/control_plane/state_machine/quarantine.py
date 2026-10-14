"""
How long a quarantine lasts before AEDE probes the endpoint again (ADR 0027).

A quarantine that only an operator can end leaves a customer cut off until
someone notices. Instead it is a timed hold, like a circuit breaker's open
state: when the hold expires the endpoint moves to RECOVERING and is probed.
If the probes fail it is quarantined again for twice as long, up to a cap,
so a dead endpoint is checked less and less often but never forgotten.

Pure functions: the caller supplies every time and count.
"""
from datetime import datetime, timedelta
from typing import Optional


def quarantine_hold_seconds(base_seconds: int, max_seconds: int, relapses: int) -> int:
    """
    The hold for a quarantine that follows `relapses` failed recoveries.

    0 for `base_seconds` disables automatic probing: only an operator
    restore ends the quarantine, as before ADR 0027.
    """
    if base_seconds <= 0:
        return 0
    return min(base_seconds * (2 ** max(relapses, 0)), max(max_seconds, base_seconds))


def hold_expired(
    quarantined_at: Optional[datetime],
    now: datetime,
    hold_seconds: int,
) -> bool:
    """True when a quarantine that began at `quarantined_at` should be probed."""
    if hold_seconds <= 0 or quarantined_at is None:
        return False
    return now - quarantined_at >= timedelta(seconds=hold_seconds)
