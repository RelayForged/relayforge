# Demo Runbook

The live demo of RelayForge in one place: how to start clean, what to click, what the audience should see, and what to do when something misbehaves. The full script with roles is in `docs/demo_implementation_plan.md` ("10-Minute Demo Script").

## Before the audience arrives (10 minutes ahead)

1. Start Docker Desktop.
2. Reset and verify:
   ```powershell
   powershell -ExecutionPolicy Bypass -File demo\reset.ps1
   ```
   - This **deletes all stack data**, rebuilds, waits for health and runs `demo/smoke_check.py`.
   - It must end with `All checks passed.` and `Ready: http://localhost:8090`.
   - The first build needs the internet (PyPI, npm, Docker Hub). If a build step times out, run it again.
3. Open `http://localhost:8090` in the browser. Check the top right shows **Connected**.
4. The smoke check leaves the endpoint AVAILABLE with some history. That is fine.

To re-check later without wiping data: `.venv\Scripts\python.exe demo\smoke_check.py`, about 3 minutes.

## The run

| Step | Click | Expect | Typical time |
|---|---|---|---|
| Normal | ▶ Normal | AVAILABLE, ERI 100, ✓ verified signature badges | immediate |
| Early Degradation | ⚠ Early Degradation | OBSERVED_DEGRADATION, yellow band | 10–30 s |
| Instability | 🔴 Instability | UNSTABLE, orange band, toast | 10–30 s |
| Full Outage | 💀 Full Outage | QUARANTINED, red banner, DLQ fills | about 10 s |
| Recovery | 🔄 Recovery | RECOVERING for about 10 s, then AVAILABLE | about 10 s |
| Replay | Replay All (DLQ panel) | parked deliveries succeed | a few seconds |
| Fallback | Simulate AEDE outage | Tier 2 banner, Tier 3 after 30 s | 30 s |
| Fallback end | Bring AEDE back | banner clears | about 1 s |
| Benchmark | Benchmark tab → Run all strategies | grouped bars; C has the fewest wasted retries | about 1 s |
| Noisy neighbour | Multi-Endpoint tab | healthy endpoint starved under A, protected under C | about 1 s |
| Journal | State Transition Journal | the whole journey in order | — |

## What to say (accurate claims only)

- **ERI** is a 0–100 score from seven signals. The radar shows each signal's health score; the outer edge is healthy.
- **State machine:** degradation and recovery use different thresholds (hysteresis), so the endpoint does not flap.
- **Isolation:** QUARANTINED stops HTTP to the endpoint. Events are kept, never dropped, and replay creates new delivery records; history is never edited.
- **Recovery:** after a restore the endpoint must show 25 good observations before it is AVAILABLE again.
- **Fallback:** this is a *simulated* AEDE outage. AEDE runs inside the dispatcher, so we cut it off rather than kill a process. Tier 2 is the in-memory policy cache, not Redis.
- **Benchmark:** C cuts wasted retries by about 80% against A and 67% against B, with the same success rate. C's total retry delay is *higher*, because it deliberately waits. Do not present delay or MTTR as a win.
- **Signatures:** every request is HMAC-signed; the badge is the receiving simulator's own verification.

## When something misbehaves

| Symptom | Likely cause | Do this |
|---|---|---|
| Top right says Disconnected | Dashboard container restarting | Wait 10 s; it reconnects and rebuilds its charts from Kafka |
| Instability goes all the way to QUARANTINED | ERI noise on a short window | Say "a sustained failure rate tips it into isolation", click Recovery, carry on |
| Stuck QUARANTINED after Recovery | Restore did not reach the dispatcher | Click **Restore Endpoint** on the red banner |
| Endpoint stays RECOVERING | No traffic, so no probes | Click ▶ Normal; probation counts deliveries |
| DLQ panel empty after Full Outage | Outage was short | Let Full Outage run 20 s before Recovery |
| Fallback banner never reaches Tier 3 | Cache TTL not applied | Check `docker compose logs dispatcher` for "TTL 30s"; otherwise narrate Tier 2 only |
| Anything else | — | `docker compose ps`, then `docker compose restart <service>`. Last resort: `demo\reset.ps1` (about 3 min) |

## After the demo

`POST http://localhost:8000/demo/stop` stops the event publisher; it also stops on `docker compose down`.
