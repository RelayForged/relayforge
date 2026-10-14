"""
A merchant's online store, as far as payments are concerned.

This is the *receiving* side of Demo 2: a merchant of the PayFlow payment
gateway, which notifies it of payments through Relay. It does what a real
merchant's webhook handler should:

- verifies every request's signature before trusting it;
- treats the payment id as the idempotency key, so a redelivered or
  replayed notification never books the same payment twice;
- answers quickly (200) and does its work after.

The inbox lives in a JSON-lines file on a volume, so stopping the container
to simulate an outage does not wipe it.

    MERCHANT_ID=acme MERCHANT_NAME="Acme Store" uvicorn examples.merchant_store.app:app
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from relay.sdk import verify_signature

MERCHANT_ID = os.environ.get("MERCHANT_ID", "merchant")
MERCHANT_NAME = os.environ.get("MERCHANT_NAME", MERCHANT_ID)
SIGNING_SECRET = os.environ.get("RELAY_WEBHOOK_SIGNING_SECRET", "")
DATA_FILE = Path(os.environ.get("MERCHANT_DATA_DIR", "/data")) / "payments.jsonl"
# A notification this much later than the payment was held somewhere on the
# way, e.g. while this store was down. Shown so the replay is visible.
DELAYED_AFTER_SECONDS = 5

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(f"store.{MERCHANT_ID}")

app = FastAPI(title=f"{MERCHANT_NAME} — payments inbox")
_lock = threading.Lock()
_rejected = 0


def _load() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        return []
    with DATA_FILE.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _append(record: dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, Any]:
    global _rejected
    body = await request.body()
    if not verify_signature(SIGNING_SECRET, body, request.headers):
        _rejected += 1
        logger.warning("Rejected a request with a missing or invalid signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    event = json.loads(body)
    payment = event.get("payload", {})
    received_at = datetime.now(timezone.utc)
    occurred_at = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))

    key = f"{event['event_type']}:{payment.get('payment_id')}"
    with _lock:
        if any(p["key"] == key for p in _load()):
            # Already booked: a redelivery or a replay. Acknowledge, do nothing.
            logger.info("Duplicate notification for %s ignored", key)
            return {"status": "duplicate"}
        _append(
            {
                "key": key,
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "payment_id": payment.get("payment_id"),
                "amount": payment.get("amount"),
                "currency": payment.get("currency", "INR"),
                "method": payment.get("method"),
                "customer": payment.get("customer"),
                "merchant_id": payment.get("merchant_id"),
                "occurred_at": occurred_at.isoformat(),
                "received_at": received_at.isoformat(),
                "delay_seconds": round((received_at - occurred_at).total_seconds(), 1),
                "attempt": request.headers.get("X-Relay-Attempt-Number"),
                "verified": True,
            }
        )
    logger.info("Booked %s for %s", key, payment.get("amount"))
    return {"status": "ok"}


@app.get("/payments")
def payments() -> dict[str, Any]:
    with _lock:
        rows = _load()
    return {
        "merchant_id": MERCHANT_ID,
        "merchant_name": MERCHANT_NAME,
        "rejected_signatures": _rejected,
        "payments": rows,
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def inbox() -> str:
    return (
        INBOX_HTML.replace("__NAME__", MERCHANT_NAME)
        .replace("__ID__", MERCHANT_ID)
        .replace("__DELAYED__", str(DELAYED_AFTER_SECONDS))
    )


INBOX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ — payments</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#0f172a; --muted:#64748b; --ok:#15803d; --bad:#b91c1c; --warn:#b45309; --line:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--ink); }
  header { padding:24px 32px 8px; }
  h1 { margin:0; font-size:28px; }
  .sub { color:var(--muted); margin-top:4px; }
  .stats { display:flex; gap:16px; padding:16px 32px; flex-wrap:wrap; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 18px; min-width:160px; }
  .stat b { display:block; font-size:24px; }
  .stat span { color:var(--muted); font-size:13px; }
  table { width:calc(100% - 64px); margin:8px 32px 32px; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:10px 14px; border-bottom:1px solid var(--line); font-size:14px; }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  tr.new td { animation: flash 1.2s ease-out; }
  @keyframes flash { from { background:#fef9c3; } to { background:transparent; } }
  .amt { font-variant-numeric: tabular-nums; font-weight:600; }
  .captured { color:var(--ok); font-weight:600; }
  .failed { color:var(--bad); font-weight:600; }
  .ok { color:var(--ok); }
  .delayed { color:var(--warn); font-size:12px; }
  .mono { font-family: ui-monospace, Consolas, monospace; font-size:12px; color:var(--muted); }
  .empty { padding:40px; text-align:center; color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>__NAME__</h1>
  <div class="sub">Payment notifications from PayFlow, delivered by Relay · merchant id <span class="mono">__ID__</span></div>
</header>
<div class="stats">
  <div class="stat"><b id="count">0</b><span>payments received</span></div>
  <div class="stat"><b id="total">₹0.00</b><span>captured</span></div>
  <div class="stat"><b id="delayed">0</b><span>arrived late (held, then delivered)</span></div>
  <div class="stat"><b id="rejected">0</b><span>rejected: bad signature</span></div>
</div>
<table>
  <thead><tr><th>Received</th><th>Payment</th><th>Amount</th><th>Status</th><th>Customer</th><th>Signature</th><th></th></tr></thead>
  <tbody id="rows"><tr><td colspan="7" class="empty">No payments yet.</td></tr></tbody>
</table>
<script>
  const seen = new Set();
  const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  async function refresh() {
    try {
      const data = await (await fetch("/payments")).json();
      const rows = data.payments.slice().reverse();
      document.getElementById("count").textContent = rows.length;
      document.getElementById("total").textContent = inr(rows.filter(r => r.event_type === "payment.captured").reduce((a, r) => a + Number(r.amount || 0), 0));
      document.getElementById("delayed").textContent = rows.filter(r => r.delay_seconds > __DELAYED__).length;
      document.getElementById("rejected").textContent = data.rejected_signatures;
      if (!rows.length) return;
      document.getElementById("rows").innerHTML = rows.map(r => {
        const status = r.event_type === "payment.captured" ? '<span class="captured">captured</span>' : '<span class="failed">failed</span>';
        const late = r.delay_seconds > __DELAYED__ ? `<span class="delayed">held ${r.delay_seconds}s</span>` : "";
        const cls = seen.has(r.key) ? "" : "new";
        return `<tr class="${cls}"><td>${new Date(r.received_at).toLocaleTimeString()}</td><td class="mono">${esc(r.payment_id)}</td><td class="amt">${inr(r.amount)}</td><td>${status}</td><td>${esc(r.customer)} · ${esc(r.method)}</td><td class="ok">✓ verified</td><td>${late}</td></tr>`;
      }).join("");
      rows.forEach(r => seen.add(r.key));
    } catch (e) { /* the store is reloading; try again next tick */ }
  }
  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>
"""
