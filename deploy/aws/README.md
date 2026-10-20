# Relay on AWS (two machines)

Demo 2 as a real deployment: Relay is a hosted platform on one EC2
instance, the merchants' stores are servers on another, and PayFlow (the
gateway) calls Relay over the internet from your laptop.

```
 laptop                      EC2 "relay" (t3.large)              EC2 "stores" (t3.small)
 PayFlow  ── HTTP :8000 ──▶  API → Kafka → dispatcher ── HTTP ──▶  Acme Store     :8071
 browser  ── :8090 ───────▶  dashboard     (AEDE)      webhooks    Bluebird Books :8072
```

Relay never assumes the stores are nearby: it delivers to whatever URL the
merchant registered. Here those are the stores instance's public address,
so every webhook crosses the network, and a stopped store is a real
timeout or refused connection.

> **No authentication yet.** The dashboard, API and simulator are open to
> anyone who has the Relay address. Fine for a demo; add auth before
> leaving it running or sharing the address widely.

## Prerequisites

- AWS CLI v2 with working credentials (`aws configure`, an IAM user's keys,
  not the root account's). `aws sts get-caller-identity` must succeed.
- Git Bash (for the `.sh` scripts) and the repo's `.venv` (for PayFlow and
  the smoke check).
- Region: `ap-south-1` (Mumbai) unless `AWS_REGION` is set.

## First time

```bash
BUDGET_EMAIL=you@example.com bash deploy/aws/provision.sh   # instances, firewall, fixed IPs (~3 min)
bash deploy/aws/deploy.sh                                   # ship HEAD, build, start (~10 min first time)
```

`deploy.sh` prints the URLs. Then onboard the merchants from your laptop
(PowerShell):

```powershell
deploy\aws\payflow.ps1 onboard acme http://<stores-ip>:8071/webhook
deploy\aws\payflow.ps1 onboard bluebird http://<stores-ip>:8072/webhook
deploy\aws\payflow.ps1 pay acme 499 --count 5 --interval 1
```

## Day to day

| Command | What it does |
|---|---|
| `bash deploy/aws/start.sh` | Start the instances and containers. Run before working or a demo. |
| `bash deploy/aws/stop.sh` | Stop both instances. Data and merchants are kept. |
| `bash deploy/aws/status.sh` | Instance states, deployed commit, URLs. |
| `bash deploy/aws/deploy.sh` | Ship the latest **commit** (uncommitted changes are not sent). |
| `bash deploy/aws/reset.sh` | Clean demo start: wipe all data and merchants, then smoke check. |
| `bash deploy/aws/teardown.sh` | Delete everything. Nothing left billing. |
| `RELAY_HOST=<relay-ip> python demo/smoke_check.py` | Smoke check the deployment. |

Addresses are Elastic IPs, so they survive stop/start: registered webhook
URLs and bookmarks keep working.

If your network changes (different Wi-Fi), SSH is refused until you re-run
`provision.sh`, which adds the new address. The dashboard is unaffected.

## Showing failure for real

Demo 1 (dashboard scenario buttons) still drives the simulator, now on the
Relay instance. For Demo 2:

```bash
# Take Acme's server down
ssh -i deploy/aws/.state/relay-demo-key.pem ubuntu@<stores-ip> 'docker stop store-acme'
deploy\aws\payflow.ps1 pay acme 250 --count 8 --interval 1
# Bring it back: Relay probes, recovers and replays the held payments
ssh -i deploy/aws/.state/relay-demo-key.pem ubuntu@<stores-ip> 'docker start store-acme'
```

Bluebird keeps receiving its payments the whole time: one merchant's
outage does not hold up another's.

## Cost

| Item | Running | Stopped |
|---|---|---|
| Relay `t3.large` | ~$0.09/hour | — |
| Stores `t3.small` | ~$0.02/hour | — |
| 2 Elastic IPs | ~$0.01/hour | ~$0.01/hour |
| Disks (30 + 16 GB gp3) | ~$4/month | ~$4/month |

About $80/month if left running, a few dollars a week if stopped when not in
use. `BUDGET_EMAIL` on `provision.sh` adds an email alarm at $16 of a $20
monthly budget.

## What the scripts change

- `docker-compose.yml`: the API, dashboard, simulator and stores bind to
  `RELAY_PUBLIC_BIND` (`127.0.0.1` locally, `0.0.0.0` on the instances).
  Kafka, Postgres and Redis stay on localhost on every machine.
- `RELAY_CORS_ORIGINS` is passed through, set to the dashboard's public
  origin on the Relay instance.
- One random `RELAY_WEBHOOK_SIGNING_SECRET` (in `.state/`) is written to both
  instances: Relay signs with it, the stores verify with it.
- State (instance ids, IPs, SSH key, secret, merchant list) lives in
  `deploy/aws/.state/`, which is gitignored. Losing it means losing SSH
  access; `teardown.sh` still finds everything by its `Project` tag.
