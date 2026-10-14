# Decision: Demo-grade Kubernetes manifests, validated offline

- **Context:** T10 closes the MVP checklist item for Kubernetes manifests. `docker compose up` remains the demo path; the manifests show how the stack deploys.

- **Decision 1 — Demo topology:** one replica each of Kafka (StatefulSet, replication factor 1), Postgres (StatefulSet) and Redis; Prometheus and Grafana on `emptyDir`. `RELAY_ENV=production`, so the compose default credentials are refused and the API needs `RELAY_API_KEYS`. `secret.yaml` holds `CHANGE_ME` placeholders, not the compose dev passwords.
  - **Why:** a real multi-broker, replicated data tier is a separate project; claiming it here would be false. Production mode exercises the existing safety checks instead of bypassing them.

- **Decision 2 — Services reuse the compose hostnames** (`api`, `dispatcher`, `simulator`, `kafka`, `postgres`, `redis`), while workloads are named `relay-*`.
  - **Why:** `SIMULATOR_URL`, the Kafka bootstrap and `deploy/prometheus.yml` work unchanged in both environments, so the scrape config has one source.

- **Decision 3 — The dispatcher gets a CPU HPA by default and a KEDA ScaledObject as an alternative, never both.**
  - **Why:** KEDA creates its own HPA for its target, and two HPAs on one Deployment fight over replicas. The ScaledObject watches the `relay-dispatcher` consumer group with no `topic`, so lag is summed over all four topics the dispatcher reads; naming only `events.business` would ignore retry and replay backlog. Maximum replicas is 3 to match `KAFKA_TOPIC_PARTITIONS`, since extra consumers beyond the partition count do no work.

- **Decision 4 — The dispatcher ships without probes for now.** The probes are written, commented, in its Deployment.
  - **Why:** its only possible listener is the metrics port, and nothing opens it until the dispatcher calls `start_metrics_server()` (Person B). A probe on 9100 could never pass, and a liveness probe would restart-loop the pod.

- **Decision 5 — Add `postgres/migrate-job.yaml`,** which was not in the original file list.
  - **Why:** compose runs `alembic upgrade head` in a `migrator` service; without an equivalent the cluster has no tables.

- **Known limit:** scaling the dispatcher past one replica splits per-endpoint ERI state, because the policy cache and AEDE state are in process memory and messages are keyed by `event_id`. The README says to run one dispatcher replica for the demo and any AEDE evaluation. Fixing it needs shared state and is out of scope.

- **Verification:** `kubeconform -strict` over all 26 resources (KEDA CRD schema included) plus a reference check. Not applied to a cluster; `kubectl --dry-run=client` needs an API server.
