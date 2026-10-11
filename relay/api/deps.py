"""FastAPI dependencies. Repositories wrap the shared async engine."""
from relay.data_plane.replay.manager import ReplayManager
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.infrastructure.persistence.event_repository import EventRepository
from relay.infrastructure.persistence.subscription_repository import SubscriptionRepository


def get_event_repository() -> EventRepository:
    return EventRepository()


def get_endpoint_repository() -> EndpointRepository:
    return EndpointRepository()


def get_subscription_repository() -> SubscriptionRepository:
    return SubscriptionRepository()


def get_replay_manager() -> ReplayManager:
    return ReplayManager()
