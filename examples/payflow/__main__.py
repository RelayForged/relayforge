"""
PayFlow operator console for Demo 2.

    python -m examples.payflow onboard acme http://store-acme:8070/webhook
    python -m examples.payflow pay acme 499
    python -m examples.payflow pay acme 250 --count 10
    python -m examples.payflow pay bluebird 1299 --fail
    python -m examples.payflow recover acme
    python -m examples.payflow track <event_id>
    python -m examples.payflow merchants

Which merchant maps to which Relay endpoint is PayFlow's own data, kept in
a small JSON file (PAYFLOW_STATE, on a volume in compose).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

from relay.sdk import RelayAPIError, RelayConnectionError

from examples.payflow import gateway

STATE_FILE = Path(os.environ.get("PAYFLOW_STATE", "/data/merchants.json"))
CUSTOMERS = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Saanvi", "Vihaan", "Anaya"]
METHODS = ["upi", "card", "netbanking", "wallet"]


def load_merchants() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_merchants(merchants: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(merchants, indent=2), encoding="utf-8")


def merchant_or_exit(merchants: dict, merchant_id: str) -> dict:
    if merchant_id not in merchants:
        sys.exit(f"Merchant '{merchant_id}' is not onboarded. Run: onboard {merchant_id} <webhook-url>")
    return merchants[merchant_id]


def cmd_onboard(args) -> None:
    merchants = load_merchants()
    if args.merchant in merchants:
        print(f"{args.merchant} is already onboarded:")
        print(json.dumps(merchants[args.merchant], indent=2))
        return
    webhook = gateway.onboard_merchant(args.merchant, args.webhook_url)
    merchants[args.merchant] = {**asdict(webhook), "webhook_url": args.webhook_url}
    save_merchants(merchants)
    print(f"Onboarded {args.merchant}")
    print(f"  webhook        {args.webhook_url}")
    print(f"  endpoint       {webhook.endpoint_id}")
    print(f"  subscription   {webhook.subscription_id}  (payment.captured, payment.failed; channel '{args.merchant}')")


def cmd_pay(args) -> None:
    merchant_or_exit(load_merchants(), args.merchant)
    for i in range(args.count):
        customer = random.choice(CUSTOMERS)
        method = random.choice(METHODS)
        event_id = gateway.notify_payment(
            args.merchant, args.amount, customer=customer, method=method, failed=args.fail
        )
        kind = "FAILED " if args.fail else "captured"
        print(f"{kind} ₹{args.amount:,.2f} from {customer} via {method:<10} -> event {event_id}")
        if i + 1 < args.count:
            time.sleep(args.interval)


def cmd_recover(args) -> None:
    merchant = merchant_or_exit(load_merchants(), args.merchant)
    requested = gateway.recover_merchant(merchant["endpoint_id"])
    print(f"Restore requested for {args.merchant}; {requested} missed notification(s) queued for replay.")


def cmd_track(args) -> None:
    with gateway.relay() as client:
        event = client.get_event(args.event_id)
    print(json.dumps(event.__dict__, indent=2, default=str))


def cmd_merchants(args) -> None:
    print(json.dumps(load_merchants(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="payflow", description="PayFlow operator console (Demo 2)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("onboard", help="register a merchant's webhook URL")
    p.add_argument("merchant")
    p.add_argument("webhook_url")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser("pay", help="take a payment and notify the merchant")
    p.add_argument("merchant")
    p.add_argument("amount", type=float)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--interval", type=float, default=0.3, help="seconds between payments")
    p.add_argument("--fail", action="store_true", help="send payment.failed instead")
    p.set_defaults(func=cmd_pay)

    p = sub.add_parser("recover", help="restore a merchant's endpoint and replay what it missed")
    p.add_argument("merchant")
    p.set_defaults(func=cmd_recover)

    p = sub.add_parser("track", help="look up an event")
    p.add_argument("event_id")
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("merchants", help="list onboarded merchants")
    p.set_defaults(func=cmd_merchants)

    args = parser.parse_args()
    try:
        args.func(args)
    except RelayConnectionError as exc:
        sys.exit(f"Cannot reach Relay at {gateway.RELAY_URL}: {exc}")
    except RelayAPIError as exc:
        sys.exit(f"Relay rejected the request ({exc.status_code}): {exc.detail}")


if __name__ == "__main__":
    main()
