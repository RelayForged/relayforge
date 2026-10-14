# Relay on Kubernetes (demo-grade manifests)

These manifests run the Relay stack in a `relay` namespace: Kafka, Postgres, Redis, the API, the dispatcher, the dashboard backend, the simulator, Prometheus and Grafana.

**They are not part of the demo script.** The demo runs on `docker compose up`. These manifests close the "Kubernetes manifests" item in the MVP checklist and show how the stack deploys; they were validated offline and have **not** been applied to a cluster (see [Verification](#verification)).

Demo-grade means: one Kafka broker (replication factor 1), one Postgres instance with no backups, one Redis with no persistence, Prometheus and Grafana on `emptyDir` storage. Do not treat this as a production topology.

## Before you apply anything

```bash
kubectl config current-context   # make sure this is the cluster you mean
```

`kubectl` applies to whatever context is current. Check it first, every time.

## Layout

```
deploy/k8s/
  namespace.yaml  configmap.yaml  secret.yaml
  kafka/statefulset.yaml          # + headless Service "kafka"
  postgres/statefulset.yaml       # + headless Service "postgres"
  postgres/migrate-job.yaml       # alembic upgrade head (not in the original T10 list)
  redis/deployment.yaml           # + Service "redis"
  relay-api/        deployment, service, ingress, hpa
  relay-dispatcher/ deployment, hpa, scaledobject   # hpa XOR scaledobject
  relay-dashboard/  deployment, service
  relay-simulator/  deployment, service
  prometheus/deployment.yaml      # + Service "prometheus"
  grafana/deployment.yaml         # + Service "grafana"
```

Workloads are named `relay-*`; their **Services** use the docker-compose hostnames (`api`, `dispatcher`, `dashboard`, `simulator`, `kafka`, `postgres`, `redis`). That is deliberate: `SIMULATOR_URL`, the Kafka bootstrap address and `deploy/prometheus.yml` (targets `api:8000` and `dispatcher:9100`) work unchanged in both environments.

## 1. Build and publish the image

All four Relay workloads and the migration Job use one image, `relay:latest`, built from the repo `Dockerfile`.

```bash
docker build -t relay:latest .
# local cluster: load it, e.g.  minikube image load relay:latest   /   kind load docker-image relay:latest
# remote cluster: push to your registry and change `image:` in the four deployments and migrate-job.yaml
```

## 2. Replace the secret placeholders

`deploy/k8s/secret.yaml` contains `CHANGE_ME` for every value. These are **not** the docker-compose dev credentials. Edit the file (do not commit real values) or create the secret another way. Postgres and Redis passwords are interpolated into connection URLs, so use URL-safe characters (letters, digits, `-`, `_`).

| Key | Used by |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | Postgres, migrate Job, API, dispatcher, dashboard |
| `REDIS_PASSWORD` | Redis, API, dispatcher, dashboard |
| `RELAY_WEBHOOK_SIGNING_SECRET` | dispatcher (signs), simulator (verifies) |
| `RELAY_API_KEYS` | API. Format `owner_id:secret`; clients send the secret as `X-API-Key` |
| `GRAFANA_ADMIN_PASSWORD` | Grafana |

The ConfigMap sets `RELAY_ENV=production`. Outside `dev` the persistence layer refuses the compose default database credentials, and the API answers **503** to every request until `RELAY_API_KEYS` is set. So unlike compose, you must send `X-API-Key` to the API and the demo routes.

## 3. Apply, in this order

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/secret.yaml

# data stores
kubectl apply -f deploy/k8s/kafka/statefulset.yaml
kubectl apply -f deploy/k8s/postgres/statefulset.yaml
kubectl apply -f deploy/k8s/redis/deployment.yaml
kubectl -n relay rollout status statefulset/kafka statefulset/postgres --timeout=300s
kubectl -n relay rollout status deployment/redis --timeout=120s

# schema: wait for completion before starting the apps
kubectl apply -f deploy/k8s/postgres/migrate-job.yaml
kubectl -n relay wait --for=condition=complete job/relay-migrate --timeout=300s

# applications
kubectl apply -f deploy/k8s/relay-simulator/
kubectl apply -f deploy/k8s/relay-api/deployment.yaml -f deploy/k8s/relay-api/service.yaml -f deploy/k8s/relay-api/hpa.yaml
kubectl apply -f deploy/k8s/relay-dispatcher/deployment.yaml
kubectl apply -f deploy/k8s/relay-dashboard/

# one dispatcher autoscaler (see below)
kubectl apply -f deploy/k8s/relay-dispatcher/hpa.yaml

# observability
kubectl -n relay create configmap prometheus-config \
  --from-file=prometheus.yml=deploy/prometheus.yml --dry-run=client -o yaml | kubectl apply -f -
kubectl -n relay create configmap grafana-datasources \
  --from-file=deploy/grafana/provisioning/datasources --dry-run=client -o yaml | kubectl apply -f -
kubectl -n relay create configmap grafana-dashboard-provider \
  --from-file=deploy/grafana/provisioning/dashboards --dry-run=client -o yaml | kubectl apply -f -
kubectl -n relay create configmap grafana-dashboards \
  --from-file=deploy/grafana/dashboards --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/k8s/prometheus/ -f deploy/k8s/grafana/

# optional: expose the ingestion API (needs an ingress controller; host is a placeholder)
kubectl apply -f deploy/k8s/relay-api/ingress.yaml
```

Do **not** run `kubectl apply -R -f deploy/k8s`: it would apply both dispatcher autoscalers (below) and the Deployments before their ConfigMaps exist.

`deploy/prometheus.yml` and `deploy/grafana/` are the single source for observability config; the `create configmap` commands load them, so nothing is duplicated into YAML here. Re-run them after editing those files.

Prometheus, Grafana, the dashboard and the simulator are not exposed by the ingress. Reach them with port-forward:

```bash
kubectl -n relay port-forward svc/grafana 3000:3000
kubectl -n relay port-forward svc/prometheus 9090:9090
kubectl -n relay port-forward svc/dashboard 8090:8090
```

## Dispatcher autoscaling: pick one

`relay-dispatcher/hpa.yaml` and `relay-dispatcher/scaledobject.yaml` are alternatives, not a pair. KEDA creates its own HPA for the workload it scales; a second HPA on the same Deployment makes the two fight over the replica count.

| | `hpa.yaml` (default) | `scaledobject.yaml` |
|---|---|---|
| Signal | CPU utilization, 70% | Kafka consumer lag |
| Needs | metrics-server | [KEDA](https://keda.sh) installed |
| Replicas | 1 to 3 | 1 to 3 |

To switch to lag-based scaling:

```bash
kubectl -n relay delete hpa relay-dispatcher
kubectl apply -f deploy/k8s/relay-dispatcher/scaledobject.yaml
```

The ScaledObject watches consumer group `relay-dispatcher` with **no `topic` set**, so lag is summed across all four topics the dispatcher reads (`events.business`, `events.internal.delivery`, `events.internal.endpoint`, `events.internal.replay`). Naming only `events.business` would ignore the retry and replay backlog. It adds one replica per 50 messages of lag (`lagThreshold`), polling every 15 s with a 120 s cooldown.

The maximum of 3 equals `KAFKA_TOPIC_PARTITIONS` in the ConfigMap: a consumer group cannot use more members than a topic has partitions, so extra replicas would idle. Topics are created with that partition count once; changing the value later does not resize existing topics.

### Caveat: scaling the dispatcher beyond one replica splits ERI state

The dispatcher keeps its policy cache (`InMemoryPolicyCache`) and the AEDE/ERI state in process memory, and Kafka messages are keyed by `event_id`, not by endpoint. With more than one dispatcher replica, telemetry for a single endpoint is spread across replicas, so each computes its own ERI and state for it. Autoscaling is configured because the MVP checklist asks for it, but **the demo and any evaluation of AEDE behavior should run with one dispatcher replica**. Fixing this means shared state (Redis-backed cache and a shared telemetry window), which is outside this task.

The dashboard and simulator are fixed at one replica for the same kind of reason: their live state is in process memory.

## Probes

| Workload | Readiness / liveness |
|---|---|
| API, simulator | HTTP `/docs` (same as the compose healthcheck) |
| Dashboard | HTTP `/api/chart/endpoints` (same as compose) |
| Kafka | TCP 9092 |
| Postgres | `pg_isready` |
| Redis | `redis-cli ping` |
| Prometheus, Grafana | `/-/ready` and `/-/healthy`, `/api/health` |
| **Dispatcher** | **none yet** |

The dispatcher is a Kafka consumer with no HTTP route. Its only listener will be the Prometheus metrics port (9100), and nothing opens that port until the dispatcher calls `start_metrics_server()` at startup, which is Person B's part of T7. A probe on 9100 today could never pass, and a liveness probe would restart-loop the pod. The probes are written out, commented, in `relay-dispatcher/deployment.yaml`; uncomment them once that call lands. Until then Prometheus reports the `dispatcher` target as down.

## Verification

Validated offline, without a cluster:

```bash
kubeconform -strict -summary -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  deploy/k8s
# Summary: 26 resources found in 20 files - Valid: 26, Invalid: 0, Errors: 0, Skipped: 0
```

The second `-schema-location` supplies the KEDA CRD schema, so the `ScaledObject` is checked against its real schema rather than skipped. Beyond schema, the manifests were checked for broken references: every `secretKeyRef` / `configMapKeyRef` key exists, every Service selector matches a pod and a declared port, every probe port is declared, every autoscaler targets an existing workload, and the dispatcher maximum does not exceed `KAFKA_TOPIC_PARTITIONS`.

**Not done:** `kubectl apply --dry-run=client` (it needs a reachable API server for discovery) and an apply to a real cluster. So it is not yet confirmed that the pods become ready, that the migration Job completes, or that Prometheus scrapes the `api` target in-cluster. The compose equivalents of those checks pass; the in-cluster ones are untested.
