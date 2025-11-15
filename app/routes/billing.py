"""
Billing webhook endpoints.

Handles Stripe webhook events for subscription lifecycle management.
"""

import structlog
from fastapi import APIRouter, HTTPException, Request

from app.deps.db import TransactionSessionDep
from app.services import billing_service, stripe_service
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["Billing"])


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    session: TransactionSessionDep,
):
    """
    Handle Stripe webhook events.

    This endpoint receives events from Stripe about subscription changes.
    """
    if not settings.IS_CLOUD:
        raise HTTPException(
            status_code=404,
            detail="Billing is not enabled in this environment",
        )

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        event = stripe_service.construct_webhook_event(payload, sig_header)
    except ValueError:
        logger.error("Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception as e:
        logger.error("Invalid signature", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    event_data = event["data"]["object"]

    logger.info("Received Stripe webhook", event_type=event_type, event_id=event["id"])

    try:
        if event_type == "checkout.session.completed":
            await billing_service.handle_checkout_completed(
                session, event_data, event["id"]
            )
        elif event_type == "customer.subscription.created":
            await billing_service.handle_subscription_created(
                session, event_data, event["id"]
            )
        elif event_type == "customer.subscription.updated":
            await billing_service.handle_subscription_updated(
                session, event_data, event["id"]
            )
        elif event_type == "customer.subscription.deleted":
            await billing_service.handle_subscription_deleted(
                session, event_data, event["id"]
            )
        elif event_type == "invoice.payment_failed":
            await billing_service.handle_payment_failed_event(
                session, event_data, event["id"]
            )
        elif event_type == "invoice.payment_succeeded":
            await billing_service.handle_payment_succeeded_event(
                session, event_data, event["id"]
            )
        else:
            logger.info("Unhandled event type", event_type=event_type)

        return {"status": "success"}
    except Exception as e:
        logger.error("Error processing webhook", error=str(e), event_type=event_type)
        raise HTTPException(status_code=500, detail="Error processing webhook")
