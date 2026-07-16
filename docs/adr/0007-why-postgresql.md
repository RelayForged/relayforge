# ADR 0007: Use PostgreSQL as the Primary Database

## Status

Accepted

## Context

The platform requires persistent storage for:
- Events (immutable, append-only after creation)
- Endpoints and Subscriptions (configuration data, updated infrequently)
- DeliveryAttempts (high write volume, immutable after creation)
- DeliveryPolicies (versioned, immutable)
- PolicyEvaluations and ExplainabilityRecords (immutable, high volume)
- EndpointState history (time-series-like, append-heavy)
- Telemetry snapshots (high volume, used for ERI computation)

The storage system must satisfy:
- **ACID transactions**: Events must be persisted durably before Kafka publication. The "persist before deliver" guarantee requires transactional writes.
- **Relational queries**: The Dashboard API joins Events, Deliveries, DeliveryAttempts, and Endpoints. Rich SQL querying is necessary.
- **JSON support**: Event payloads and ExplainabilityRecord metadata are semi-structured. Native JSON column support avoids separate document stores.
- **High write throughput**: DeliveryAttempts and Telemetry are written on every delivery execution.
- **Self-hostable**: The platform must run on any infrastructure. Managed-only databases conflict with the open-source self-hosting goal.

### Options Evaluated

**PostgreSQL** — Battle-tested relational database. ACID-compliant. Excellent JSON support (`jsonb`). Strong Python ecosystem (`asyncpg`, SQLAlchemy, Alembic). Self-hostable and cloud-managed (RDS, Cloud SQL, Aurora). Mature tooling for migrations, monitoring, and backups.

**MySQL / MariaDB** — Comparable ACID guarantees. Weaker JSON support than PostgreSQL. Less expressive query planner for complex analytical queries needed by the Dashboard API. Rejected.

**MongoDB** — Document model fits semi-structured event payloads. However, multi-document ACID transactions were added late and are still not as mature as PostgreSQL. The relational structure of the domain model (Events → Deliveries → Attempts → Policies) is a poor fit for a document database. Rejected.

**CockroachDB** — Distributed SQL with PostgreSQL wire protocol compatibility. Provides horizontal scalability. Adds significant operational complexity for a v1 platform. PostgreSQL can scale to the throughput required for v1 without distributed infrastructure. Rejected for v1; noted as a potential future option for multi-region deployments.

**SQLite** — Suitable for local development and testing only. Cannot support concurrent writers at production scale. Rejected for production.

**Amazon DynamoDB** — Serverless, highly scalable, fully managed. However, it introduces cloud provider lock-in, which conflicts with self-hosting and open-source goals. Schema design must work around DynamoDB's access pattern constraints. Rejected.

## Decision

Use **PostgreSQL 15+** as the primary database.

PostgreSQL is the sole durable store for all platform data. Redis is used as a secondary cache for hot policy data and ERI snapshots (see ADR 0003 for caching rationale).

Key schema design decisions that follow from this choice:

- Event payloads stored as `jsonb` — indexed, queryable, without a separate document store.
- ExplainabilityRecord metadata stored as `jsonb` — semi-structured reasoning is captured without schema proliferation.
- All primary keys use UUIDs (`uuid` type) — globally unique identifiers without coordination.
- All timestamps use `timestamptz` — timezone-aware throughout.
- DeliveryAttempt and Telemetry tables are append-only. No rows are updated after creation.
- Migrations managed by Alembic (SQLAlchemy's migration tool).

## Consequences

**Positive:**
- Single source of truth for all relational platform data. No data consistency issues across multiple databases.
- `asyncpg` provides high-performance async PostgreSQL access compatible with FastAPI's async model.
- `jsonb` columns provide flexible semi-structured storage for payloads and explainability metadata without sacrificing query performance.
- ACID transactions guarantee that an Event is committed to the database before its Kafka publication is acknowledged. The "persist before deliver" guarantee is enforced at the database level.
- SQLAlchemy + Alembic provide a mature migration workflow. Schema changes are versioned, reversible, and reproducible.
- PostgreSQL runs in Docker Compose for local development, Kubernetes for production, and any managed PostgreSQL service for cloud deployment. No platform lock-in.

**Negative:**
- High write volume for DeliveryAttempts and Telemetry requires careful table design (partitioning by time, appropriate indexes) to avoid write contention.
- PostgreSQL is a single-node primary by default. For high availability, a read replica or PostgreSQL HA solution (e.g., Patroni) is required in production.
- Operational knowledge required: vacuuming, autovacuum tuning, connection pooling (PgBouncer for high connection counts).

**Mitigations:**
- Partition the DeliveryAttempts and Telemetry tables by month to manage table size.
- PgBouncer for connection pooling in Kubernetes deployments.
- Terraform provisions PostgreSQL with appropriate HA configuration for production deployments.
- Redis caches hot reads (current endpoint policy, ERI score) to reduce PostgreSQL read pressure on the delivery path.

PostgreSQL is the correct choice because it provides ACID guarantees, rich relational querying, native JSON support, and self-hostability — all required by the platform without introducing the operational complexity of distributed SQL or the flexibility trade-offs of NoSQL.
