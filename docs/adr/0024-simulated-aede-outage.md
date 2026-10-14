# ADR 0024: Simulated AEDE Outage for the Fallback Demonstration

## Status

Accepted (Person B, 2026-10-14)

## Context

The MVP checklist requires the three-tier policy fallback (ADR 0011) to be demonstrated "by stopping the AEDE process". AEDE has no process of its own: `AEDEService` runs inside the Dispatcher, and stopping the Dispatcher stops delivery too. The fallback could not be shown on the running stack, and the dashboard had no way to tell which tier was serving.

Two further gaps made a demonstration impossible even with a way to cut AEDE off:

- The Tier 2 cache TTL was hard-coded to 300 s in both the Dispatcher and `AEDEService`. Reaching Tier 3 would take five minutes.
- Nothing reported the serving tier outside the Dispatcher's logs.

## Decision

1. **An operator switch, not a process kill.** `POST /demo/aede {"available": bool}` publishes `{"action": "aede_availability", "available": ...}` to `events.internal.endpoint`, next to restore. The route is mounted only when the demo router is.
2. **What "unavailable" means in the Dispatcher.**
   - Policy resolution skips Tier 1, so the cached policy (Tier 2) is served until it expires, then the static Strategy A defaults (Tier 3).
   - The Worker stops feeding telemetry to AEDE, as it would if AEDE were down. Attempts and telemetry are still persisted.
   - Restore commands are ignored with a warning.
   - Switching back restores Tier 1 and the telemetry feed.
3. **One cache TTL.** `RELAY_POLICY_CACHE_TTL_SECONDS` (default 300) is read once by both the Dispatcher and `AEDEService` through `policy_cache_ttl_from_env()`. Compose sets 30.
4. **Tier visibility.**
   - When an endpoint's serving tier changes, the Dispatcher logs a warning and publishes `{"type": "policy_tier", endpoint_id, tier, at}` to `events.internal.policy`.
   - Normal Tier 1 service is not announced.
   - The dashboard consumer records the tier, and `/ws` carries it as `policy_tier`.
   - The UI shows a banner for Tier 2 and Tier 3, and a switch next to the scenario presets.

## Consequences

- The fallback is demonstrable on the live stack: Tier 2 immediately, Tier 3 after 30 s, Tier 1 within a second of switching back. Deliveries continue throughout, which is the claim that Tier 3 is degraded delivery, not a halt.
- This simulates an outage. It does not prove the Dispatcher survives a crashed AEDE process, because there is none to crash. Narrate it as "AEDE unavailable".
- Tier 2 is served from the in-memory cache. There is no Redis-backed cache yet, so the demo must not claim Redis.
- While AEDE is unavailable it records no evaluations, so the dashboard's ERI and state stop moving. That is accurate: nothing is evaluating.
- The switch state lives in the Dispatcher's memory and resets to "available" on restart. It is a demo control, not operational state.
