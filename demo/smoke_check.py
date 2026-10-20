"""
Pre-demo smoke check: drives the running compose stack the way the
dashboard does, and exits non-zero if any step fails.

    python demo/smoke_check.py
    RELAY_HOST=13.200.1.2 python demo/smoke_check.py   # a deployed stack

Checks every route the UI calls (with the browser's CORS origin), then runs
Normal -> Full Outage -> restore -> Recovery over the live WebSocket and
checks the endpoint's states, then walks the policy fallback with a
simulated AEDE outage. Leaves the demo publisher stopped and AEDE available.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx
import websockets

# The stack's host: local compose by default, or a deployment (deploy/aws).
HOST = os.environ.get("RELAY_HOST", "127.0.0.1")
UI_ORIGIN = "http://localhost:8090" if HOST == "127.0.0.1" else f"http://{HOST}:8090"
API = f"http://{HOST}:8000"
DASHBOARD = f"http://{HOST}:8090"
SIMULATOR = f"http://{HOST}:8088"
WS = f"ws://{HOST}:8090/ws"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def call(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response | None:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        check(False, f"{method} {url}", f"{type(exc).__name__}: {exc}")
        return None
    cors = response.headers.get("access-control-allow-origin") == UI_ORIGIN
    check(
        response.is_success and cors,
        f"{method} {url}",
        f"{response.status_code}{'' if cors else ', no CORS header for the UI origin'}",
    )
    return response


async def watch_states(endpoint_id: str, until: set[str], timeout: float) -> list[str]:
    """States seen on the WebSocket, until one in `until` appears or time runs out."""
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    async with websockets.connect(f"{WS}?endpoint_id={endpoint_id}", origin=UI_ORIGIN) as ws:
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if message.get("source") != "live":
                check(False, "WebSocket source is live", f"got {message.get('source')!r}")
                return seen
            if not seen or seen[-1] != message["state"]:
                seen.append(message["state"])
            if message["state"] in until:
                break
    return seen


async def watch_tiers(endpoint_id: str, until: int, timeout: float) -> list[int]:
    """Serving policy tiers seen on the WebSocket, until `until` appears or time runs out."""
    seen: list[int] = []
    deadline = time.monotonic() + timeout
    async with websockets.connect(f"{WS}?endpoint_id={endpoint_id}", origin=UI_ORIGIN) as ws:
        while time.monotonic() < deadline:
            tier = json.loads(await asyncio.wait_for(ws.recv(), timeout=10)).get("policy_tier")
            if not seen or seen[-1] != tier:
                seen.append(tier)
            if tier == until:
                break
    return seen


async def main() -> int:
    client = httpx.Client(timeout=60, headers={"Origin": UI_ORIGIN})

    print("UI")
    response = call(client, "GET", f"{DASHBOARD}/")
    check(response is not None and "<div id=\"root\">" in response.text, "dashboard serves the React build")

    print("Routes the UI reads")
    for url in (
        f"{DASHBOARD}/api/chart/benchmark/multi-endpoint",
        f"{DASHBOARD}/api/chart/benchmark/results",
        f"{SIMULATOR}/simulator/config",
        f"{SIMULATOR}/simulator/deliveries?limit=5",
        f"{API}/demo/scenarios",
    ):
        call(client, "GET", url)
    run = call(client, "POST", f"{DASHBOARD}/api/chart/benchmark/run", json={"scenario_ids": [10]})
    if run is not None and run.is_success:
        check(set(run.json()["comparison"]) == {"c_vs_a", "c_vs_b"}, "benchmark comparison computed")

    print("Live run")
    response = call(client, "POST", f"{API}/demo/scenario", json={"scenario": "normal"})
    if response is None or not response.is_success:
        return 1
    endpoint_id = response.json()["endpoint_id"]
    seen = await watch_states(endpoint_id, {"AVAILABLE"}, 30)
    check(seen[-1:] == ["AVAILABLE"], "Normal: AVAILABLE", " -> ".join(seen))

    call(client, "POST", f"{API}/demo/scenario", json={"scenario": "full_outage"})
    seen = await watch_states(endpoint_id, {"QUARANTINED"}, 45)
    check(seen[-1:] == ["QUARANTINED"], "Full Outage: QUARANTINED", " -> ".join(seen))

    dlq = call(client, "GET", f"{DASHBOARD}/api/chart/dlq?limit=50&endpoint_id={endpoint_id}")
    items = dlq.json()["items"] if dlq is not None and dlq.is_success else []
    check(
        all(item["endpoint_id"] == endpoint_id for item in items),
        "DLQ holds only this endpoint",
        f"{len(items)} items",
    )

    call(client, "POST", f"{API}/demo/scenario", json={"scenario": "recovery"})
    seen = await watch_states(endpoint_id, {"AVAILABLE"}, 60)
    check(
        "RECOVERING" in seen and seen[-1:] == ["AVAILABLE"],
        "Recovery: RECOVERING -> AVAILABLE",
        " -> ".join(seen),
    )

    if items:
        replay = call(
            client,
            "POST",
            f"{API}/replay",
            json={"endpoint_id": endpoint_id, "limit": 50},
        )
        if replay is not None and replay.is_success:
            check(replay.json()["requested"] > 0, "Replay All requested deliveries",
                  f"{replay.json()['requested']} requested")

    print("Policy fallback")
    call(client, "POST", f"{API}/demo/scenario", json={"scenario": "normal"})
    call(client, "POST", f"{API}/demo/aede", json={"available": False})
    tiers = await watch_tiers(endpoint_id, 3, 60)
    check(tiers[-2:] == [2, 3], "AEDE outage: Tier 2 then Tier 3", " -> ".join(map(str, tiers)))
    call(client, "POST", f"{API}/demo/aede", json={"available": True})
    tiers = await watch_tiers(endpoint_id, 1, 20)
    check(tiers[-1:] == [1], "AEDE back: Tier 1", " -> ".join(map(str, tiers)))

    received = client.get(f"{SIMULATOR}/simulator/deliveries?limit=20").json()
    statuses = {item["signature_status"] for item in received}
    check("verified" in statuses, "simulator verified HMAC signatures", ", ".join(sorted(statuses)))

    call(client, "POST", f"{API}/demo/stop")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
