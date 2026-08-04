# ADR 0009: ERI Windowing Strategy

**Date**: 2026-07-15  
**Status**: Accepted  
**Author**: B (Sam)

## Context

The Endpoint Reliability Index (ERI) requires a window of recent deliveries to compute a reliable health score for a given endpoint. A key decision is whether this window should be defined by **time** (e.g., "all deliveries in the last 15 minutes") or by **attempt-count** (e.g., "the last 100 delivery attempts").

Time-based windows introduce significant complications:
- High-volume endpoints may push thousands of events through the window in a minute, skewing the weighting of recent errors versus historical stability.
- Low-volume endpoints may have only one or two events in the time window, causing massive, volatile swings in ERI (a single failure could drop the ERI to 0).
- Time-based decay requires constant recalculation even when no events are flowing.

## Decision

We will use **attempt-count only** for the ERI windowing, with **no time-based filter**.

The ERI will always evaluate a fixed-size sliding window of the most recent *N* DeliveryAttempts for that endpoint (e.g., the last 50 attempts), regardless of how long it took to accumulate them.

## Consequences

- **Stability**: Low-volume endpoints will not experience wild ERI swings from a single failure, as their historical successes remain in the window until displaced by new attempts.
- **Simplicity**: No background jobs are needed to decay time-based scores. ERI is recomputed synchronously (or asynchronously via events) only when a new DeliveryAttempt completes.
- **Trade-off**: For very low-volume endpoints, a stale failure from weeks ago might persist in the window. However, this is acceptable for the MVP as a failure is still a failure until proven otherwise by a successful ping or new event delivery.
