# Sprint Kickoff: Pre-Week 1 & Week 1 Day 1 Summary

Welcome to the sprint! As B (Sam), I've completed the foundational setup so that A and C can begin work on the Dispatcher, Worker, and Simulator components immediately.

Please read through this document before beginning your tasks. Do not modify the entity shapes or infrastructure configuration without raising it with me first.

---

## 1. Repository Structure
The directory map outlined in `arch.md` has been fully scaffolded. Empty directories have been committed with `.gitkeep` files.
- `platform/` (contains `kernel`, `control_plane`, `data_plane`, `adapters`, `infrastructure`, `api`, `sdk`, `dashboard`)
- `docs/` (contains `adr`, `sprints`)
- `benchmark/`

> [!IMPORTANT]
> **File placement is not up for discussion.** Please stick to the scaffolded directories.

## 2. Infrastructure (Docker Compose)
The `docker-compose.yml` file is located at the root of the project. It includes:
- **Kafka** (KRaft mode, no ZooKeeper) on ports `9092` / `29092`.
- **PostgreSQL** on port `5432` (User: `relay`, Pass: `relaypassword`, DB: `relaydb`).
- **Redis** on port `6379`.

> [!TIP]
> Run `docker compose up -d` and ensure all containers are healthy before starting your development.

## 3. Kafka Topics
The following topics have been designated. Even though only `events.business` is used this sprint, all are named now so nobody invents alternatives.
- `events.business`
- `events.internal.delivery`
- `events.internal.policy`
- `events.internal.endpoint`

## 4. Domain Entities & Value Objects
I have committed the core domain entities as Pydantic models. They are fully immutable (`frozen=True`) to adhere to our event-driven architecture requirements.

**Value Objects** (`platform/kernel/value_objects/identifiers.py`):
- `CorrelationID`
- `CausationID`

**Entities** (`platform/kernel/entities/`):
- `Event`
- `Endpoint`
- `Subscription`
- `Delivery`
- `DeliveryAttempt`

> [!CAUTION]
> The entities are strict and immutable. If you need to "modify" an entity (e.g., update a Delivery status), you must create a new instance with the updated fields (e.g., using `model_copy(update={...})`), rather than mutating it in place.

## 5. ERI Windowing
As documented in the newly created [ADR 0009](file:///c:/Users/sammj/MajorProject/docs/adr/0009-eri-windowing.md), the ERI windowing uses **attempt-count only**. There is **no time-based filter**. Do not reintroduce a time-based window in your implementations.

---

## Next Steps for Week 1 (Days 2-5)
- **Person A (Backend)**: Verify `docker compose up` works for you. Begin Alembic migrations for the `Event` table and build the Postgres repository. Ensure the Kafka producer wrapper abstracts `kafka-python` completely.
- **Person C (Dashboard + Simulator)**: (See sprint docs for details).
- **Person B (Me)**: I will be designing the State Transition Table for the ERI Endpoint State Machine (ADR 0008).

Let me know if anything is unclear or if you run into environment issues!
