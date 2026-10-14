# Demo 2: A Payment Gateway Integrates Relay

Demo 1 shows Relay protecting itself when an endpoint fails. Demo 2 shows how a company uses it: a payment gateway notifying its merchants of payments.

**The roles:**

| Who | In the demo | What it is |
|---|---|---|
| **PayFlow**, the gateway ("think Razorpay") | you, in a terminal | the `payflow` container: the gateway's backend, using the Relay Python SDK |
| **Acme Store** | http://localhost:8071 | a merchant's server: receives payment webhooks and shows an inbox |
| **Bluebird Books** | http://localhost:8072 | a second merchant, to show isolation |
| **Relay** | http://localhost:8090 | delivers every notification, signed, to the right merchant only |

The integration code to put on a slide is `examples/payflow/gateway.py`, about 40 lines. The merchant's receiver is `examples/merchant_store/app.py`.

## Before you start

`demo\reset.ps1` brings up both demos with empty inboxes. To start only Demo 2 on a stack that is already running:

```powershell
docker compose --profile sdk-demo up -d
```

Open both inboxes and the Relay dashboard side by side. Both inboxes are empty.

A shorthand for the steps below (PowerShell):

```powershell
function payflow { docker compose exec payflow python -m examples.payflow @args }
```

## The run (about 6 minutes)

### 1. Onboard the merchants (1 min)

Say: *"A merchant signs up with PayFlow and pastes their webhook URL into our dashboard. Behind that button, our backend makes two Relay calls."* Show `onboard_merchant` in `gateway.py`.

```powershell
payflow onboard acme http://store-acme:8070/webhook
payflow onboard bluebird http://store-bluebird:8070/webhook
```

Each prints the Relay endpoint and a subscription to `payment.captured` and `payment.failed` **on that merchant's channel**. Running it again is safe; it prints the existing ids.

### 2. Take payments (1 min)

Say: *"A customer pays Acme. PayFlow tells Relay once, and Relay owns delivery from here."*

```powershell
payflow pay acme 499
payflow pay bluebird 1299
payflow pay bluebird 350 --fail
```

What the audience sees:
- Each payment lands in **its own merchant's inbox only**, within about a second, marked ✓ verified.
- Bluebird shows the failed payment as well.
- The Relay dashboard switches to the endpoint it last evaluated.

Points:
- **Channels.** Every merchant subscribes to the same event types, but a payment is published on its merchant's channel, so only that merchant receives it.
- **Signatures.** Every webhook is HMAC-signed. The merchant's code checks it with one SDK call, `verify_signature`, and a forged request gets 401 and is counted on the inbox page.

### 3. A merchant goes down (2 min)

Say: *"Acme's server crashes during a sale."*

```powershell
docker compose stop store-acme
payflow pay acme 250 --count 8 --interval 1
payflow pay bluebird 99 --count 8
```

What the audience sees:
- **Bluebird keeps receiving at full speed.** Acme's outage does not slow anyone else down.
- The Relay dashboard shows Acme's endpoint degrading to **QUARANTINED**. Relay stops sending to it but keeps every notification.

### 4. The merchant comes back (1–2 min)

```powershell
docker compose start store-acme
payflow recover acme
```

`recover` makes two SDK calls: `restore_endpoint` (lift the quarantine) and `replay_dead_letters` (resend what was held). Over the next 10–30 seconds **all 8 missed payments appear in Acme's inbox**, marked "held Ns", each booked once.

Say: *"No merchant ever misses a payment notification, even through their own outage, and nobody had to reconcile by hand."*

**Variation: no human at all.** Skip `recover` and keep taking payments (`payflow pay acme 5 --count 30 --interval 1`). After the 30 s quarantine hold, Relay probes Acme by itself. Once 25 probes succeed, Acme is AVAILABLE again and the held payments are resent automatically (ADR 0027). If Acme is still down, the hold doubles (30 s, 60 s, … up to 4 minutes).

### 5. Optional: look one up

```powershell
payflow track <event_id printed by pay>
```

## What to say, accurately

- **At-least-once delivery.** The receiver de-duplicates on the payment id, which is what webhook receivers should always do. The inbox shows each payment once even when a retry and a replay both arrive.
- **Replay never edits history.** Replay creates new delivery records; the failed ones stay as they were.
- **Channels are routing, not access control.** Relay does not yet stop one API key from subscribing to another tenant's channel. Per-tenant authorization is future work.
- **One signing secret.** The secret is shared platform-wide in this build, not issued per merchant.
- **No API keys in compose.** The stack runs without API keys. The SDK supports `RelayClient(api_key=...)`; that is how production would authenticate.

## When something misbehaves

| Symptom | Do this |
|---|---|
| `payflow: service "payflow" is not running` | `docker compose --profile sdk-demo up -d` |
| "Merchant 'acme' is not onboarded" | Run step 1 |
| Inbox shows nothing after `pay` | Check the store is up: `docker compose ps store-acme`. Payments are held, not lost, until it is |
| Missed payments still not there 60 s after `recover` | Run `payflow recover acme` again; replay skips anything already replayed |
| Want empty inboxes again | `demo\reset.ps1` (wipes all stack data) |

## Automated check

```powershell
.venv\Scripts\python.exe demo\sdk_smoke_check.py
```

This runs steps 1–4 and checks routing, verification, isolation, quarantine and exactly-once recovery. It takes about two minutes and adds payments to both inboxes, so run it before `demo\reset.ps1`, not after.
