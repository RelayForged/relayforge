"""
Kafka Dispatcher — data plane consumer process.

Reads `events.business` (ingest) and `events.internal.delivery` (retries).
Persist-first: Kafka offset is committed after Postgres, never after HTTP.
Never imports aiokafka. Week 3–4: Strategy A only.

At-least-once (ADR 0002) is enforced by *when* offsets are committed:
a message is acknowledged only once its work has completed and persisted.
Anything still in flight at shutdown or at a crash stays uncommitted and
is redelivered. Repository writes are idempotent, so redelivery is safe.

Run:
    .venv/bin/python -m relay.data_plane.dispatcher.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from pydantic import ValidationError

from relay.adapters.webhook.adapter import WebhookDeliveryAdapter
from relay.control_plane.aede.service import AEDEService
from relay.control_plane.aede.strategies import StrategyA, StrategyB, StrategyC
from relay.data_plane.replay.manager import ReplayManager, ReplayWork
from relay.data_plane.retry_manager.manager import RetryManager, RetryWork
from relay.data_plane.workers.webhook import WebhookWorker
from relay.infrastructure.auth.signing import hmac_required_from_env
from relay.infrastructure.messaging.producer import publish, warmup as warmup_producer
from relay.infrastructure.cache import (
    InMemoryPolicyCache,
    PolicyCache,
    PolicyFallbackManager,
    policy_cache_ttl_from_env,
)
from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.messaging.admin import (
    EVENTS_INTERNAL_ENDPOINT_TOPIC,
    EVENTS_INTERNAL_POLICY_TOPIC,
    EVENTS_INTERNAL_REPLAY_TOPIC,
)
from relay.infrastructure.messaging.consumer import (
    EVENTS_BUSINESS_TOPIC,
    EVENTS_INTERNAL_DELIVERY_TOPIC,
    KafkaConsumer,
    KafkaMessage,
)
from relay.infrastructure.persistence.delivery_attempt_repository import (
    DeliveryAttemptRepository,
)
from relay.infrastructure.persistence.delivery_repository import DeliveryRepository
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.infrastructure.persistence.subscription_repository import (
    SubscriptionRepository,
)
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.policies.delivery_policy import DeliveryPolicy, IsolationAction
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore

DISPATCHER_GROUP_ID = "relay-dispatcher"

# Business messages processed concurrently. Bounded so a fan-out burst
# cannot open more DB connections than the pool has, and so blocking on
# the semaphore applies backpressure to polling.
DEFAULT_MAX_CONCURRENT_MESSAGES = 16
# Retries waiting for next_attempt_at. Each holds a RetryWork in memory
# for up to max_delay_seconds.
DEFAULT_MAX_IN_FLIGHT_RETRIES = 500
TierPublisher = Callable[..., Awaitable[None]]

logger = logging.getLogger(__name__)

# Only QUARANTINE stops HTTP (ADR 0022). PAUSE is throttled delivery: its
# policy's backoff and max_parallel apply, and the attempts it lets through
# are the telemetry that lets an UNSTABLE endpoint recover. Skipping HTTP
# under PAUSE as well left UNSTABLE with no way out.
_SKIP_HTTP = frozenset({IsolationAction.QUARANTINE})
_TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "DEAD_LETTER"})


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


class Dispatcher:
    def __init__(
        self,
        *,
        consumer: Optional[KafkaConsumer] = None,
        subscriptions: Optional[SubscriptionRepository] = None,
        deliveries: Optional[DeliveryRepository] = None,
        attempts: Optional[DeliveryAttemptRepository] = None,
        endpoints: Optional[EndpointRepository] = None,
        worker: Optional[WebhookWorker] = None,
        retry_manager: Optional[RetryManager] = None,
        fallback_manager: Optional[PolicyFallbackManager] = None,
        cache: Optional[PolicyCache] = None,
        feature_flags: Optional[FeatureFlags] = None,
        aede_service: Optional[AEDEService] = None,
        tier_publisher: Optional[TierPublisher] = None,
    ) -> None:
        self._consumer = consumer or KafkaConsumer(
            topics=(
                EVENTS_BUSINESS_TOPIC,
                EVENTS_INTERNAL_DELIVERY_TOPIC,
                EVENTS_INTERNAL_ENDPOINT_TOPIC,
                EVENTS_INTERNAL_REPLAY_TOPIC,
            ),
            group_id=DISPATCHER_GROUP_ID,
        )
        self._subscriptions = subscriptions or SubscriptionRepository()
        self._deliveries = deliveries or DeliveryRepository()
        self._attempts = attempts or DeliveryAttemptRepository()
        self._endpoints = endpoints or EndpointRepository()
        retries = retry_manager or RetryManager(
            deliveries=self._deliveries,
            attempts=self._attempts,
        )
        self._retry_manager = retries

        self._flags = feature_flags or FeatureFlags.from_env()
        self._cache = cache or InMemoryPolicyCache()
        # Handed to whichever strategy produces the policy; the Dispatcher
        # itself never decides whether a request is signed.
        hmac_required = hmac_required_from_env()
        self._fallback_manager = fallback_manager or PolicyFallbackManager(
            cache=self._cache, hmac_required=hmac_required
        )
        self._aede_service = aede_service
        # Operator-controlled AEDE outage, for demonstrating the fallback
        # hierarchy (ADR 0024). While False, Tier 1 is skipped and AEDE
        # receives no telemetry, as if its process were down.
        self._aede_available = True
        self._policy_cache_ttl = policy_cache_ttl_from_env()
        # Which tier last served each endpoint. Changes are logged and, when
        # a publisher is given, announced on the policy topic for the dashboard.
        self._policy_tiers: dict[str, int] = {}
        self._tier_publisher = tier_publisher
        # Auto-replay (ADR 0027): when AEDE reports an endpoint recovered,
        # resend what was held for it. Off unless RELAY_AUTO_REPLAY is set.
        self._auto_replay = os.environ.get("RELAY_AUTO_REPLAY", "false").lower() in ("true", "1", "yes")
        if self._auto_replay and self._aede_service is not None:
            self._aede_service.set_recovery_listener(self._replay_after_recovery)

        self._worker = worker or WebhookWorker(
            adapter=WebhookDeliveryAdapter(),
            attempts=self._attempts,
            retry_manager=retries,
            aede_service=self._aede_service,
        )
        if getattr(self._worker, "_retry_manager", None) is None:
            self._worker._retry_manager = self._retry_manager

        self._messages: set[asyncio.Task[None]] = set()
        self._scheduled: set[asyncio.Task[None]] = set()
        self._message_slots = asyncio.Semaphore(
            _int_env("DISPATCHER_MAX_CONCURRENT_MESSAGES", DEFAULT_MAX_CONCURRENT_MESSAGES)
        )
        self._retry_slots = asyncio.Semaphore(
            _int_env("DISPATCHER_MAX_IN_FLIGHT_RETRIES", DEFAULT_MAX_IN_FLIGHT_RETRIES)
        )
        self._endpoint_slots: dict[str, asyncio.Semaphore] = {}

        if self._flags.active_strategy_name == "STRATEGY_A":
            self._strategy = StrategyA(hmac_required=hmac_required)
        elif self._flags.active_strategy_name == "STRATEGY_B":
            self._strategy = StrategyB(hmac_required=hmac_required)
        else:
            self._strategy = StrategyC(hmac_required=hmac_required)

        logger.info(
            "Dispatcher initialized with strategy=%s (AEDE=%s, ERI=%s, StateMachine=%s)",
            self._strategy.strategy_name,
            self._flags.aede_enabled,
            self._flags.eri_enabled,
            self._flags.state_machine_enabled,
        )

    async def run(self) -> None:
        await self._consumer.start()
        try:
            async for message in self._consumer:
                await self._accept(message)
        finally:
            await self._drain()
            await self._consumer.close()
            await self._worker.aclose()

    async def _accept(self, message: KafkaMessage) -> None:
        if message.topic == EVENTS_INTERNAL_ENDPOINT_TOPIC:
            await self._accept_endpoint_control(message)
            return
        if message.topic == EVENTS_INTERNAL_DELIVERY_TOPIC:
            await self._accept_retry(message)
            return
        if message.topic == EVENTS_INTERNAL_REPLAY_TOPIC:
            await self._accept_replay(message)
            return
        await self._message_slots.acquire()
        task = asyncio.create_task(self._run_message(message))
        self._messages.add(task)
        task.add_done_callback(self._messages.discard)

    async def _accept_endpoint_control(self, message: KafkaMessage) -> None:
        """Handle internal endpoint control commands like manual admin restore."""
        try:
            data = json.loads(message.value)
            action = data.get("action")
            endpoint_id = data.get("endpoint_id")
            if action == "aede_availability":
                self._set_aede_available(bool(data.get("available", True)))
            elif action == "restore" and endpoint_id and self._aede_service is not None:
                if not self._aede_available:
                    logger.warning(
                        "Restore for endpoint=%s ignored: AEDE is marked unavailable",
                        endpoint_id,
                    )
                    return
                await self._aede_service.restore_and_publish(
                    endpoint_id, restored_at=datetime.now(timezone.utc)
                )
                logger.info("Dispatcher applied manual restore for endpoint=%s", endpoint_id)
        except Exception:
            logger.exception(
                "Skipping invalid endpoint control payload partition=%s offset=%s",
                message.partition,
                message.offset,
            )
        finally:
            await self._consumer.commit(message)


    async def _replay_after_recovery(self, endpoint_id: str, recovered_at: datetime) -> None:
        """Resend an endpoint's dead letters once AEDE has seen it recover."""
        try:
            result = await ReplayManager(deliveries=self._deliveries).replay_dead_letters(
                now=recovered_at, endpoint_id=endpoint_id, limit=500
            )
        except Exception:
            logger.exception("Automatic replay failed for endpoint=%s", endpoint_id)
            return
        logger.warning(
            "Endpoint %s recovered: automatic replay requested for %s held deliveries",
            endpoint_id,
            result.requested,
        )

    def _set_aede_available(self, available: bool) -> None:
        """
        Simulated AEDE outage (ADR 0024). AEDE runs in this process, so it
        cannot be stopped on its own; this cuts it off instead. Policy
        resolution skips Tier 1 and the Worker stops feeding it telemetry.
        """
        if available == self._aede_available:
            return
        self._aede_available = available
        if hasattr(self._worker, "_aede_service"):
            self._worker._aede_service = self._aede_service if available else None
        if available:
            logger.warning("AEDE marked available: policy resolution returns to Tier 1")
        else:
            logger.warning(
                "AEDE marked unavailable: serving Tier 2 cached policies (TTL %ss), then Tier 3",
                self._policy_cache_ttl,
            )

    async def _note_policy_tier(self, endpoint_id: str, tier: int, at: datetime) -> None:
        """Log and announce a change of serving tier. Normal Tier 1 service is silent."""
        previous = self._policy_tiers.get(endpoint_id)
        self._policy_tiers[endpoint_id] = tier
        if previous == tier or (previous is None and tier == 1):
            return
        logger.warning(
            "Policy tier for endpoint=%s changed %s -> %s", endpoint_id, previous, tier
        )
        if self._tier_publisher is None:
            return
        try:
            await self._tier_publisher(
                EVENTS_INTERNAL_POLICY_TOPIC,
                key=endpoint_id,
                value=json.dumps(
                    {
                        "type": "policy_tier",
                        "endpoint_id": endpoint_id,
                        "tier": tier,
                        "at": at.isoformat(),
                    }
                ).encode("utf-8"),
            )
        except Exception:
            logger.exception(
                "Could not publish policy tier change endpoint_id=%s tier=%s",
                endpoint_id,
                tier,
            )

    async def _accept_replay(self, message: KafkaMessage) -> None:
        try:
            work = ReplayWork.model_validate_json(message.value)
        except ValidationError:
            logger.warning(
                "Skipping invalid replay payload partition=%s offset=%s",
                message.partition,
                message.offset,
            )
            await self._consumer.commit(message)
            return

        await self._message_slots.acquire()
        task = asyncio.create_task(self._run_replay(work, message))
        self._messages.add(task)
        task.add_done_callback(self._messages.discard)

    async def _run_replay(self, work: ReplayWork, message: KafkaMessage) -> None:
        """
        Dispatch a replayed Event to its one subscription (ADR 0021).

        Goes through _dispatch_one like any first delivery, so the policy
        comes from the Control Plane and a paused or quarantined endpoint
        is not contacted: the Delivery is parked again instead. Committed
        only after the work is durable, so a crash redelivers the replay.
        """
        try:
            subscription = await self._subscriptions.get_by_id(work.subscription_id)
            endpoint = (
                await self._endpoints.get_by_id(subscription.endpoint_id)
                if subscription is not None
                else None
            )
            if subscription is None or endpoint is None or not endpoint.is_active:
                # Nothing a retry of this message could fix.
                logger.warning(
                    "Dropping replay replay_id=%s source_delivery_id=%s: "
                    "subscription or active endpoint no longer exists",
                    work.replay_id,
                    work.source_delivery_id,
                )
            else:
                await self._dispatch_one(
                    work.event, subscription, endpoint, datetime.now(timezone.utc)
                )
                logger.info(
                    "replay dispatched replay_id=%s source_delivery_id=%s "
                    "endpoint_id=%s event_id=%s",
                    work.replay_id,
                    work.source_delivery_id,
                    endpoint.endpoint_id,
                    work.event.event_id,
                )
        except Exception:
            # Not committed: the replay is redelivered, as for a business event.
            logger.exception(
                "Replay failed replay_id=%s source_delivery_id=%s partition=%s offset=%s",
                work.replay_id,
                work.source_delivery_id,
                message.partition,
                message.offset,
            )
            return
        else:
            await self._consumer.commit(message)
        finally:
            self._message_slots.release()

    async def _run_message(self, message: KafkaMessage) -> None:
        try:
            await self.handle_message(message)
        finally:
            self._message_slots.release()

    async def handle_message(self, message: KafkaMessage) -> None:
        try:
            event = Event.model_validate_json(message.value)
        except ValidationError:
            logger.warning(
                "Skipping invalid Kafka payload topic=%s partition=%s offset=%s",
                message.topic,
                message.partition,
                message.offset,
            )
            # Unparseable forever: acknowledge so the partition advances.
            await self._consumer.commit(message)
            return

        try:
            await self._fan_out(event)
        except Exception:
            # Deliberately not committed. The watermark stalls at this
            # offset and the message is redelivered, which is the whole
            # point of at-least-once; losing it would be at-most-once.
            logger.exception(
                "Fan-out failed event_id=%s topic=%s partition=%s offset=%s",
                event.event_id,
                message.topic,
                message.partition,
                message.offset,
            )
            return

        await self._consumer.commit(message)

    async def _accept_retry(self, message: KafkaMessage) -> None:
        try:
            work = RetryWork.model_validate_json(message.value)
        except ValidationError:
            logger.warning(
                "Skipping invalid retry payload partition=%s offset=%s",
                message.partition,
                message.offset,
            )
            await self._consumer.commit(message)
            return

        await self._retry_slots.acquire()
        task = asyncio.create_task(self._run_scheduled(work, message))
        self._scheduled.add(task)
        task.add_done_callback(self._scheduled.discard)

    async def _run_scheduled(self, work: RetryWork, message: KafkaMessage) -> None:
        """
        Wait out the backoff, then execute.

        Sleeping here is sanctioned for the Dispatcher only — the Worker
        and the Retry Manager never sleep. The offset is committed *after*
        the attempt has been executed and persisted, so a crash or a
        shutdown during the sleep redelivers the retry instead of dropping
        it in memory.
        """
        try:
            delay = (work.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            await self._execute_attempt(
                work.attempt, work.delivery, work.policy, work.event
            )
        except asyncio.CancelledError:
            # Shutdown mid-backoff: leave the offset uncommitted.
            raise
        except Exception:
            logger.exception(
                "Scheduled attempt failed delivery_id=%s attempt_number=%s",
                work.delivery.delivery_id,
                work.attempt.attempt_number,
            )
            return
        else:
            await self._consumer.commit(message)
        finally:
            self._retry_slots.release()

    async def _drain(self) -> None:
        """
        Cancel outstanding work without committing anything.

        Uncommitted offsets are redelivered on restart. Waiting out a
        300 s backoff here would stall shutdown; acknowledging it would
        silently drop a retry.
        """
        tasks = [*self._messages, *self._scheduled]
        if not tasks:
            return
        pending = self._consumer.pending_offsets()
        logger.info(
            "Draining %d in-flight task(s); uncommitted offsets: %s",
            len(tasks),
            pending or "none",
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fan_out(self, event: Event) -> None:
        targets = await self._subscriptions.list_active_targets_by_event_type(
            event.event_type, event.channels
        )
        if not targets:
            return
        computed_at = datetime.now(timezone.utc)
        results = await asyncio.gather(
            *(
                self._dispatch_one(event, subscription, endpoint, computed_at)
                for subscription, endpoint in targets
            ),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            # Re-raise so the offset is withheld. Targets that already
            # succeeded are no-ops on redelivery (idempotent saves and the
            # terminal-status guard in _dispatch_one).
            raise failures[0]

    async def _dispatch_one(
        self,
        event: Event,
        subscription: Subscription,
        endpoint: Endpoint,
        computed_at: datetime,
    ) -> None:
        delivery = await self._persist_delivery(event, subscription, computed_at)
        if delivery.status in _TERMINAL_STATUSES:
            # Already resolved by an earlier delivery of this message.
            return

        endpoint_state = getattr(endpoint, "state", EndpointState.AVAILABLE)
        if self._aede_service is not None and self._aede_available:
            # A quarantine whose hold has expired becomes RECOVERING here,
            # so this delivery is one of the probes (ADR 0027).
            endpoint_state = await self._aede_service.check_quarantine_hold(
                subscription.endpoint_id, computed_at
            )
        elif isinstance(endpoint_state, str):
            try:
                endpoint_state = EndpointState(endpoint_state)
            except ValueError:
                endpoint_state = EndpointState.AVAILABLE

        policy, tier = self._resolve_policy(
            endpoint_id=subscription.endpoint_id,
            endpoint_state=endpoint_state,
            computed_at=computed_at,
        )
        await self._note_policy_tier(subscription.endpoint_id, tier, computed_at)

        if policy.isolation_policy.action in _SKIP_HTTP:
            # Delivery row stays durable in PENDING. No attempt row is
            # written: an attempt that can never complete would block the
            # attempt-1 slot and stop this Delivery from ever dispatching.
            return

        attempt = await self._persist_attempt(delivery.delivery_id, 1, computed_at)
        if attempt is None:
            # Attempt 1 already ran; the retry chain owns this Delivery.
            return

        await self._execute_attempt(
            attempt, delivery, policy, event, endpoint=endpoint
        )

    async def _persist_delivery(
        self,
        event: Event,
        subscription: Subscription,
        computed_at: datetime,
    ) -> Delivery:
        candidate = Delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            endpoint_id=subscription.endpoint_id,
            created_at=computed_at,
            status="PENDING",
        )
        stored = await self._deliveries.save(candidate)
        if stored is not None:
            return stored
        # No-op upsert: the canonical row carries a different delivery_id.
        # One extra read, and only on a redelivery.
        existing = await self._deliveries.get_by_event_and_subscription(
            event.event_id, subscription.subscription_id
        )
        return existing or candidate

    async def _persist_attempt(
        self,
        delivery_id: str,
        attempt_number: int,
        started_at: datetime,
    ) -> Optional[DeliveryAttempt]:
        """
        Claim an attempt slot. Returns None when the attempt already
        completed and must not be re-executed.
        """
        candidate = DeliveryAttempt(
            delivery_id=delivery_id,
            attempt_number=attempt_number,
            started_at=started_at,
        )
        inserted = await self._attempts.insert_if_absent(candidate)
        if inserted is not None:
            return inserted
        existing = await self._attempts.get_by_delivery_and_number(
            delivery_id, attempt_number
        )
        if existing is None or existing.completed_at is not None:
            return None
        return existing

    async def _execute_attempt(
        self,
        attempt: DeliveryAttempt,
        delivery: Delivery,
        policy: DeliveryPolicy,
        event: Event,
        *,
        endpoint: Optional[Endpoint] = None,
    ) -> None:
        if endpoint is None:
            endpoint = await self._endpoints.get_by_id(delivery.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            reason = "endpoint missing" if endpoint is None else "endpoint inactive"
            await self._dead_letter(attempt, delivery, reason)
            return

        slot = self._endpoint_slot(
            delivery.endpoint_id, policy.concurrency_policy.max_parallel
        )
        async with slot:
            await self._worker.execute(
                attempt,
                endpoint.target_url,
                delivery.endpoint_id,
                event=event,
                delivery=delivery,
                policy=policy,
            )

    async def _dead_letter(
        self,
        attempt: DeliveryAttempt,
        delivery: Delivery,
        reason: str,
    ) -> None:
        """
        Terminate a Delivery that no retry can fix.

        A missing or deactivated endpoint is a configuration fact, not an
        endpoint-health signal, so retrying forever would leave the row
        stranded in PENDING with an attempt that never completes.
        """
        logger.warning(
            "Dead-lettering delivery_id=%s: %s", delivery.delivery_id, reason
        )
        await self._attempts.save(
            attempt.model_copy(
                update={
                    "completed_at": datetime.now(timezone.utc),
                    "success": False,
                    "error_message": f"undeliverable: {reason}",
                }
            )
        )
        await self._deliveries.save(
            delivery.model_copy(update={"status": "DEAD_LETTER"})
        )

    def _endpoint_slot(self, endpoint_id: str, max_parallel: int) -> asyncio.Semaphore:
        """
        Honor `concurrency_policy.max_parallel` (Strategy A: 1).

        The bound is fixed at the first policy seen for an endpoint —
        asyncio.Semaphore cannot be resized without dropping held slots.
        Strategy A is constant, so this is exact for Week 3-4.
        """
        slot = self._endpoint_slots.get(endpoint_id)
        if slot is None:
            slot = asyncio.Semaphore(max(1, max_parallel))
            self._endpoint_slots[endpoint_id] = slot
        return slot

    def _get_delivery_policy(
        self,
        endpoint_id: str,
        endpoint_state: EndpointState,
        computed_at: datetime,
    ) -> DeliveryPolicy:
        return self._resolve_policy(endpoint_id, endpoint_state, computed_at)[0]

    def _resolve_policy(
        self,
        endpoint_id: str,
        endpoint_state: EndpointState,
        computed_at: datetime,
    ) -> tuple[DeliveryPolicy, int]:
        """
        Queries delivery policy dynamically using the Three-Tier Policy Fallback Hierarchy (ADR 0011):
        Tier 1: Live active strategy evaluation (AEDE / Strategy C/B/A) -> updates policy cache.
        Tier 2: Redis / In-memory cached policy (within 300s TTL).
        Tier 3: Static fallback defaults (safe Strategy A baseline).
        """
        # Tier 1: Live AEDE service or strategy evaluation
        if not self._aede_available:
            pass
        elif self._flags.aede_enabled and self._aede_service is not None:
            try:
                policy = self._aede_service.get_current_policy(
                    endpoint_id, computed_at=computed_at
                )
                try:
                    self._cache.set_policy(
                        endpoint_id, policy, ttl_seconds=self._policy_cache_ttl
                    )
                except Exception:
                    pass
                return policy, 1
            except Exception as e:
                logger.warning(
                    "Tier 1 AEDE live policy evaluation failed for %s (%s); querying Tier 2 cache",
                    endpoint_id,
                    e,
                )
        elif self._flags.aede_enabled:
            try:
                eri = ERIScore.perfect(window_size=0, computed_at=computed_at)
                res = self._strategy.evaluate(
                    endpoint_id=endpoint_id,
                    endpoint_state=endpoint_state,
                    eri=eri,
                    computed_at=computed_at,
                )
                policy = res[0] if isinstance(res, tuple) else res
                try:
                    self._cache.set_policy(
                        endpoint_id, policy, ttl_seconds=self._policy_cache_ttl
                    )
                except Exception:
                    pass
                return policy, 1
            except Exception as e:
                logger.warning(
                    "Tier 1 policy evaluation failed for %s (%s); querying Tier 2 cache",
                    endpoint_id,
                    e,
                )

        # Tier 2: Cached Policy
        cached = self._fallback_manager.get_tier_2_cached_policy(
            endpoint_id, current_time=computed_at
        )
        if cached is not None:
            logger.info("Serving Tier 2 cached policy for %s", endpoint_id)
            return cached, 2

        # Tier 3: Static Fallback Policy (Strategy A defaults)
        logger.warning("Serving Tier 3 static fallback policy for %s", endpoint_id)
        return (
            self._fallback_manager.get_tier_3_static_policy(
                endpoint_id, computed_at=computed_at
            ),
            3,
        )


async def run() -> None:
    flags = FeatureFlags.from_env()
    # Pay the producer's cold-start cost (ensure_topics + AIOKafkaProducer.start,
    # ~8s combined against a real broker) once here, eagerly -- otherwise it
    # lands inside AEDEService.record_telemetry()'s 5s publish timeout on
    # whichever telemetry event happens to be the first one in, and fails
    # silently (see relay/infrastructure/messaging/producer.py::warmup).
    await warmup_producer()
    cache = InMemoryPolicyCache()
    aede = AEDEService(cache=cache) if flags.aede_enabled else None
    await Dispatcher(
        aede_service=aede, cache=cache, feature_flags=flags, tier_publisher=publish
    ).run()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
