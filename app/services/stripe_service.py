import stripe
import structlog

from app.models import Organization, Subscription, SubscriptionTier
from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _get_price_id_for_tier(tier: SubscriptionTier) -> str:
    if tier == SubscriptionTier.HOBBY:
        if not settings.STRIPE_PRICE_ID_HOBBY:
            raise ValueError("STRIPE_PRICE_ID_HOBBY is not configured")
        return settings.STRIPE_PRICE_ID_HOBBY
    elif tier == SubscriptionTier.TEAM:
        if not settings.STRIPE_PRICE_ID_TEAM:
            raise ValueError("STRIPE_PRICE_ID_TEAM is not configured")
        return settings.STRIPE_PRICE_ID_TEAM
    else:
        raise ValueError(f"No price ID for tier: {tier}")


async def get_or_create_customer(
    organization: Organization, subscription: Subscription
) -> str:
    if subscription.stripe_customer_id:
        return subscription.stripe_customer_id

    customer = stripe.Customer.create(
        name=organization.display_name,
        metadata={
            "organization_id": str(organization.id),
            "subscription_id": str(subscription.id),
        },
    )
    return customer.id


async def create_subscription_with_intent(
    organization: Organization,
    subscription: Subscription,
    target_tier: SubscriptionTier,
    session_db=None,
) -> dict:
    price_id = _get_price_id_for_tier(target_tier)
    customer_id = await get_or_create_customer(organization, subscription)

    if not subscription.stripe_customer_id:
        subscription.stripe_customer_id = customer_id
        if session_db:
            await session_db.flush()

    # Check for existing incomplete subscription to reuse
    existing_sub = _find_incomplete_subscription(customer_id, price_id)
    if existing_sub:
        client_secret = _extract_client_secret(existing_sub)
        if client_secret:
            return {
                "subscription_id": existing_sub["id"],
                "client_secret": client_secret,
            }
        # Cancel unusable incomplete subscription
        try:
            stripe.Subscription.cancel(existing_sub["id"])
        except Exception:
            pass

    stripe_sub = stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price_id}],
        payment_behavior="default_incomplete",
        payment_settings={"save_default_payment_method": "on_subscription"},
        collection_method="charge_automatically",
        metadata={
            "organization_id": str(organization.id),
            "subscription_id": str(subscription.id),
            "tier": target_tier.value,
        },
        expand=["latest_invoice.confirmation_secret"],
    )

    subscription.stripe_subscription_id = stripe_sub.id
    if session_db:
        await session_db.flush()

    # client_secret = _extract_client_secret_from_subscription(stripe_sub)
    try:
        client_secret = stripe_sub.latest_invoice.confirmation_secret.client_secret  # type: ignore
    except Exception:
        raise ValueError("No client_secret found on payment intent")

    return {"subscription_id": stripe_sub.id, "client_secret": client_secret}


def _find_incomplete_subscription(customer_id: str, price_id: str) -> dict | None:
    try:
        subscriptions = stripe.Subscription.list(
            customer=customer_id,
            status="incomplete",
            expand=["data.latest_invoice.payment_intent"],
        )
        for sub in subscriptions.data:
            for item in sub.get("items", {}).get("data", []):
                if item.get("price", {}).get("id") == price_id:
                    return sub
        return None
    except stripe.StripeError:
        return None


def _extract_client_secret(sub: dict) -> str | None:
    latest_invoice = sub.get("latest_invoice")
    if not latest_invoice:
        return None

    confirmation_secret = (
        latest_invoice.get("confirmation_secret")
        if isinstance(latest_invoice, dict)
        else getattr(latest_invoice, "confirmation_secret", None)
    )
    if not confirmation_secret:
        return None

    return (
        confirmation_secret.get("client_secret")
        if isinstance(confirmation_secret, dict)
        else getattr(confirmation_secret, "client_secret", None)
    )


def _extract_client_secret_from_subscription(stripe_sub) -> str | None:
    latest_invoice_id = stripe_sub.latest_invoice
    if isinstance(latest_invoice_id, str):
        # Expansion failed, fetch invoice
        try:
            invoice = stripe.Invoice.retrieve(
                latest_invoice_id, expand=["payment_intent"]
            )
            print(dict(invoice))
            payment_intent = invoice.payment_intent
        except Exception:
            return None
    else:
        print(latest_invoice_id)
        payment_intent = (
            latest_invoice_id.get("payment_intent")
            if isinstance(latest_invoice_id, dict)
            else getattr(latest_invoice_id, "payment_intent", None)
        )

    if not payment_intent:
        return None

    return (
        payment_intent.get("client_secret")
        if isinstance(payment_intent, dict)
        else getattr(payment_intent, "client_secret", None)
    )


async def verify_subscription_payment(stripe_subscription_id: str) -> dict:
    sub = stripe.Subscription.retrieve(
        stripe_subscription_id, expand=["latest_invoice.payment_intent"]
    )

    if sub.status == "active":
        return {
            "status": "active",
            "subscription_id": sub.id,
            "message": "Subscription is active",
        }

    latest_invoice = sub.latest_invoice
    if isinstance(latest_invoice, str):
        latest_invoice = stripe.Invoice.retrieve(
            latest_invoice, expand=["payment_intent"]
        )

    invoice_status = (
        latest_invoice.get("status")
        if isinstance(latest_invoice, dict)
        else latest_invoice.status
    )

    if invoice_status == "paid":
        sub = stripe.Subscription.retrieve(stripe_subscription_id)
        return {
            "status": sub.status,
            "subscription_id": sub.id,
            "invoice_status": invoice_status,
            "message": "Invoice is paid",
        }

    payment_intent = (
        latest_invoice.get("payment_intent")
        if isinstance(latest_invoice, dict)
        else getattr(latest_invoice, "payment_intent", None)
    )

    if invoice_status == "open" and payment_intent:
        pi_id = (
            payment_intent.get("id")
            if isinstance(payment_intent, dict)
            else getattr(payment_intent, "id", None)
        )
        if pi_id:
            pi = stripe.PaymentIntent.retrieve(pi_id)
            pi_status = pi.get("status") if isinstance(pi, dict) else pi.status

            if pi_status == "succeeded":
                invoice_id = (
                    latest_invoice.get("id")
                    if isinstance(latest_invoice, dict)
                    else latest_invoice.id
                )
                stripe.Invoice.pay(invoice_id)
                sub = stripe.Subscription.retrieve(stripe_subscription_id)
                return {
                    "status": sub.status,
                    "subscription_id": sub.id,
                    "invoice_status": "paid",
                    "payment_intent_status": pi_status,
                    "message": "Invoice paid successfully",
                }

            if pi_status == "requires_action":
                return {
                    "status": "incomplete",
                    "subscription_id": sub.id,
                    "invoice_status": invoice_status,
                    "payment_intent_status": pi_status,
                    "message": "Payment requires additional authentication",
                    "requires_action": True,
                }

            if pi_status == "processing":
                return {
                    "status": "incomplete",
                    "subscription_id": sub.id,
                    "invoice_status": invoice_status,
                    "payment_intent_status": pi_status,
                    "message": "Payment is still processing",
                }

    return {
        "status": sub.status,
        "subscription_id": sub.id,
        "invoice_status": invoice_status,
        "message": f"Invoice status: {invoice_status}",
    }


def create_customer_portal_session(customer_id: str, return_url: str) -> dict:
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return {"url": session.url}


async def cancel_subscription(stripe_subscription_id: str) -> None:
    stripe.Subscription.modify(stripe_subscription_id, cancel_at_period_end=True)


def construct_webhook_event(payload: bytes, sig_header: str):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")

    return stripe.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )


def set_default_payment_method_if_none(
    customer_id: str, payment_method_id: str
) -> bool:
    print("WOOO")
    try:
        customer = stripe.Customer.retrieve(customer_id)
        print(customer)
        default_pm = customer.get("invoice_settings", {}).get("default_payment_method")

        if default_pm:
            return False

        stripe.Customer.modify(
            customer_id,
            invoice_settings={"default_payment_method": payment_method_id},
        )
        return True
    except stripe.StripeError:
        return False


def find_subscription_by_organization(organization_id: str) -> dict | None:
    try:
        customers = stripe.Customer.search(
            query=f'metadata["organization_id"]:"{organization_id}"',
        )
        if not customers.data:
            return None

        customer = customers.data[0]
        subscriptions = stripe.Subscription.list(
            customer=customer.id,
            status="all",
            limit=1,
        )
        if not subscriptions.data:
            return None

        return subscriptions.data[0]
    except stripe.StripeError:
        return None


def get_default_payment_method(customer_id: str) -> dict | None:
    try:
        customer = stripe.Customer.retrieve(
            customer_id, expand=["invoice_settings.default_payment_method"]
        )
        print(customer)
        default_pm = customer.get("invoice_settings", {}).get("default_payment_method")
        if not default_pm:
            return None

        if isinstance(default_pm, str):
            default_pm = stripe.PaymentMethod.retrieve(default_pm)

        card = default_pm.get("card", {})
        return {
            "payment_method_id": default_pm.get("id"),
            "brand": card.get("brand"),
            "last4": card.get("last4"),
            "exp_month": card.get("exp_month"),
            "exp_year": card.get("exp_year"),
        }
    except stripe.StripeError as e:
        print(e)
        return None


def list_customer_invoices(customer_id: str, limit: int = 10) -> list[dict]:
    try:
        invoices = stripe.Invoice.list(customer=customer_id, limit=limit)
        return [
            {
                "id": inv.id,
                "number": inv.number,
                "amount_due": inv.amount_due,
                "amount_paid": inv.amount_paid,
                "currency": inv.currency,
                "status": inv.status,
                "created": inv.created,
                "period_start": inv.period_start,
                "period_end": inv.period_end,
                "pdf_url": inv.invoice_pdf,
                "hosted_invoice_url": inv.hosted_invoice_url,
            }
            for inv in invoices.data
        ]
    except stripe.StripeError:
        return []
