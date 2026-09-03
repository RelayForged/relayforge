"""
Subscription registration. Week 2: event_types + endpoint_id, status ACTIVE.

Resolves endpoint_id before insert so a missing endpoint returns 404
instead of a raw IntegrityError from the FK.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from relay.api.deps import get_endpoint_repository, get_subscription_repository
from relay.api.schemas import CreateSubscriptionRequest
from relay.api.security import require_owner
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.infrastructure.persistence.subscription_repository import SubscriptionRepository
from relay.kernel.entities.subscription import Subscription

router = APIRouter(tags=["subscriptions"])


@router.post(
    "/subscriptions",
    response_model=Subscription,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    body: CreateSubscriptionRequest,
    _owner_id: str = Depends(require_owner),
    endpoints: EndpointRepository = Depends(get_endpoint_repository),
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
) -> Subscription:
    endpoint = await endpoints.get_by_id(body.endpoint_id)
    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {body.endpoint_id} not found",
        )

    subscription = Subscription(
        endpoint_id=body.endpoint_id,
        event_types=body.event_types,
        status="ACTIVE",
    )
    try:
        await subscriptions.save(subscription)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {body.endpoint_id} not found",
        ) from exc
    return subscription
