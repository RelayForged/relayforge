"""
Demo 2 smoke check: PayFlow onboards two merchants, takes payments, one
merchant goes down, comes back, and must end up with every payment.

    docker compose --profile sdk-demo up -d
    python demo/sdk_smoke_check.py

Drives the PayFlow container with the same commands as demo/SDK_DEMO.md and
reads each store's inbox. Exits non-zero if any check fails. Takes about
two minutes, most of it waiting for the outage and the recovery.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import httpx

STORES = {"acme": "http://127.0.0.1:8071", "bluebird": "http://127.0.0.1:8072"}
WEBHOOKS = {"acme": "http://store-acme:8070/webhook", "bluebird": "http://store-bluebird:8070/webhook"}
DASHBOARD = "http://127.0.0.1:8090"
OUTAGE_PAYMENTS = 8

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def compose(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *args], capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def payflow(*args: str) -> str:
    return compose("exec", "-T", "payflow", "python", "-m", "examples.payflow", *args)


def inbox(merchant: str) -> list[dict]:
    return httpx.get(f"{STORES[merchant]}/payments", timeout=10).json()["payments"]


def wait_for(merchant: str, count: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    seen = -1
    while time.monotonic() < deadline:
        try:
            seen = len(inbox(merchant))
        except httpx.HTTPError:
            seen = -1
        if seen >= count:
            return seen
        time.sleep(1)
    return seen


def endpoint_state(endpoint_id: str) -> str:
    try:
        return httpx.get(f"{DASHBOARD}/api/chart/endpoints/{endpoint_id}/policy", timeout=10).json().get(
            "state_after", "?"
        )
    except Exception:
        return "?"


def main() -> int:
    print("Onboarding")
    for merchant, url in WEBHOOKS.items():
        payflow("onboard", merchant, url)
    merchants = json.loads(payflow("merchants"))
    check(set(merchants) >= {"acme", "bluebird"}, "both merchants onboarded")
    acme_endpoint = merchants["acme"]["endpoint_id"]

    base = {m: len(inbox(m)) for m in STORES}

    print("Routing")
    payflow("pay", "acme", "499")
    payflow("pay", "bluebird", "1299")
    got = {m: wait_for(m, base[m] + 1, 20) for m in STORES}
    check(got["acme"] == base["acme"] + 1, "Acme received its payment", f"{got['acme'] - base['acme']} new")
    check(got["bluebird"] == base["bluebird"] + 1, "Bluebird received its payment", f"{got['bluebird'] - base['bluebird']} new")
    for merchant in STORES:
        others = [p for p in inbox(merchant) if p.get("merchant_id") not in (merchant, None)]
        check(not others, f"{merchant} holds only its own payments", f"{len(others)} foreign")
        check(all(p["verified"] for p in inbox(merchant)), f"{merchant}: every payment signature-verified")

    print("Acme outage")
    compose("stop", "store-acme")
    payflow("pay", "acme", "250", "--count", str(OUTAGE_PAYMENTS), "--interval", "1")
    payflow("pay", "bluebird", "99", "--count", str(OUTAGE_PAYMENTS), "--interval", "0.3")
    blue = wait_for("bluebird", got["bluebird"] + OUTAGE_PAYMENTS, 30)
    check(blue == got["bluebird"] + OUTAGE_PAYMENTS, "Bluebird unaffected by Acme's outage", f"{blue - got['bluebird']} of {OUTAGE_PAYMENTS}")

    deadline = time.monotonic() + 60
    state = endpoint_state(acme_endpoint)
    while state != "QUARANTINED" and time.monotonic() < deadline:
        time.sleep(2)
        state = endpoint_state(acme_endpoint)
    check(state == "QUARANTINED", "AEDE quarantined Acme", state)

    print("Acme recovery")
    compose("start", "store-acme")
    time.sleep(5)
    print("  " + payflow("recover", "acme").strip())
    expected = got["acme"] + OUTAGE_PAYMENTS
    final = wait_for("acme", expected, 120)
    check(final == expected, "Acme received every payment it missed, once each", f"{final - got['acme']} of {OUTAGE_PAYMENTS}")
    keys = [p["key"] for p in inbox("acme")]
    check(len(keys) == len(set(keys)), "no payment booked twice")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
