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
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    event_data = event["data"]["object"]
    event_id = event["id"]

    logger.info("Received Stripe webhook", event_type=event_type, event_id=event_id)

    handlers = {
        "checkout.session.completed": billing_service.handle_checkout_completed,
        "customer.subscription.created": billing_service.handle_subscription_created,
        "customer.subscription.updated": billing_service.handle_subscription_updated,
        "customer.subscription.deleted": billing_service.handle_subscription_deleted,
        "invoice.payment_failed": billing_service.handle_payment_failed_event,
        "invoice.payment_succeeded": billing_service.handle_payment_succeeded_event,
    }

    handler = handlers.get(event_type)
    if handler:
        await handler(session, event_data, event_id)

    return {"status": "success"}
