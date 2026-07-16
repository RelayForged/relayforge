# ADR 0001: Use Apache Kafka as the Messaging Backbone

## Status

Accepted

## Context

The Event Delivery Platform requires a messaging backbone to decouple event ingestion from delivery execution. This backbone must satisfy several requirements that are not typical of standard task queues:

- **Durability**: Events must survive worker failures, restarts, and infrastructure outages without loss.
- **Replay**: Failed or quarantined events must be replayable after endpoint recovery — this is fundamental to the platform's reliability guarantees.
- **Partitioning**: Events must be partitioned by endpoint or tenant to enable parallel delivery while preserving per-endpoint ordering.
- **Multi-consumer**: Multiple independent consumers (Dispatcher, Metrics, Audit Service, Replay Manager) must be able to read the same events independently without competing.
- **Research reproducibility**: Experiments comparing delivery strategies must operate on identical event datasets. An append-only log is the natural substrate for this.

The choice of messaging backbone directly influences the platform's ability to provide at-least-once delivery, deterministic replay, and reproducible benchmarking.

## Decision

Use **Apache Kafka** as the sole messaging backbone.

Kafka provides a durable, partitioned, append-only event log. Events published to Kafka are retained for a configurable retention period, allowing consumers to replay from any point in the log. Consumer groups allow multiple independent services to consume the same events at their own pace without competing for messages.

All internal platform events — delivery attempts, policy evaluations, telemetry, replay requests — are published to Kafka topics. The platform never delivers transient in-memory events.

### Alternatives Considered

**RabbitMQ** — Provides reliable message queuing with flexible routing. However, RabbitMQ does not natively support message replay. Once a message is acknowledged, it is removed from the queue. Replay would require a separate storage layer, defeating the simplicity of a unified backbone. Rejected.

**Amazon SQS** — Managed service with simple operations. Does not support replay (messages are deleted after consumption). Additionally introduces cloud provider lock-in, which conflicts with the platform's self-hostable, open-source goals. Rejected.

**Redis Streams** — Append-only log with consumer groups, similar in concept to Kafka. Suitable for smaller deployments. However, Redis Streams is less mature for high-throughput production workloads, has weaker ecosystem tooling, and lacks Kafka's partition-level parallelism semantics at scale. Rejected for v1; may be reconsidered as a lightweight deployment option in future.

**In-memory queue (asyncio, Celery)** — Eliminates the operational complexity of running Kafka. However, in-memory queues cannot guarantee durability across restarts and do not support replay. Any worker crash during delivery would result in event loss. Rejected.

## Consequences

**Positive:**
- Events are durable from the moment of ingestion. Worker crashes cannot cause event loss.
- Deterministic replay is natively supported — the Replay Manager simply re-consumes from a specified offset.
- Research reproducibility is straightforward: experiments reprocess the same Kafka-persisted event dataset.
- Multiple consumers (Dispatcher, Audit, Metrics) read independently without coordination.
- Kafka's partition model maps cleanly onto per-endpoint delivery parallelism.

**Negative:**
- Kafka introduces operational complexity: broker management, ZooKeeper or KRaft, topic configuration, retention policies.
- Local development requires running a Kafka cluster (mitigated via Docker Compose).
- Kafka is heavier than a simple task queue for small event volumes.
- Teams unfamiliar with Kafka have a steeper onboarding curve.

**Mitigations:**
- Docker Compose provides a zero-configuration local Kafka environment.
- KRaft mode (Kafka without ZooKeeper) reduces operational complexity for production deployments.
- Terraform provisions Kafka infrastructure reproducibly.

Kafka was chosen because deterministic replay is an architectural requirement, not an optional feature. It enables the research contribution (reproducible benchmark experiments), the reliability guarantee (events survive failures), and the operational capability (replay after quarantine recovery).
