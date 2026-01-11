import inspect
import json
from typing import Any, Type
from uuid import UUID

from fastapi import Request
from fastapi.responses import RedirectResponse
from markupsafe import Markup
from sqladmin import ModelView, action
from sqlalchemy.dialects.postgresql import JSONB

import app.models as models_module
from app.models import Base, Organization
from app.services.billing_service import sync_subscription_from_stripe

# Maximum characters to show in list view before truncating
MAX_DISPLAY_LENGTH = 80


def get_model_column_list(model: Type[Base]) -> list[str]:
    return [column.name for column in model.__table__.columns]


def get_searchable_columns(model: Type[Base]) -> list[str]:
    """Get columns that should be searchable (IDs and name fields)."""
    searchable = []
    for column in model.__table__.columns:
        column_name = column.name

        # Include all ID fields (including UUIDs - SQLAdmin casts to string for search)
        if column_name.endswith("_id") or column_name == "id":
            searchable.append(column_name)
            continue

        # Include name-related fields
        if "name" in column_name.lower():
            searchable.append(column_name)
            continue

        # Include email fields
        if "email" in column_name.lower():
            searchable.append(column_name)
            continue

        # Include type/status/key fields for filtering
        if column_name in ("type", "alias", "key", "prefix", "path", "method"):
            searchable.append(column_name)
            continue

    return searchable


def get_columns_to_hide_in_list(model: Type[Base]) -> list[str]:
    """Get columns that should be hidden in list view (large/sensitive fields)."""
    hidden = []
    for column in model.__table__.columns:
        column_name = column.name
        column_type = column.type

        # Hide JSONB columns in list view (show in detail)
        if isinstance(column_type, JSONB):
            hidden.append(column_name)
            continue

        # Hide encrypted/sensitive fields
        if any(
            x in column_name.lower()
            for x in ("encrypted", "hash", "password", "secret")
        ):
            hidden.append(column_name)
            continue

    return hidden


def truncate_value(value: Any, max_length: int = MAX_DISPLAY_LENGTH) -> str:
    """Truncate a value for display, handling different types."""
    if value is None:
        return ""

    if isinstance(value, dict):
        text = json.dumps(value, default=str)
    elif isinstance(value, list):
        text = json.dumps(value, default=str)
    elif isinstance(value, UUID):
        return str(value)
    else:
        text = str(value)

    if len(text) <= max_length:
        return text

    return text[:max_length] + "…"


def create_column_formatters(model: Type[Base]) -> dict:
    """Create column formatters for large fields."""
    formatters = {}

    for column in model.__table__.columns:
        column_name = column.name
        column_type = column.type

        # Format JSONB columns
        if isinstance(column_type, JSONB):
            formatters[column_name] = lambda m, a, col=column_name: Markup(
                f'<code style="font-size: 11px; white-space: pre-wrap; max-width: 300px; display: block; overflow: hidden;">'
                f"{truncate_value(getattr(m, col), 200)}</code>"
            )
            continue

        # Format long text fields
        if str(column_type).startswith("TEXT"):
            formatters[column_name] = lambda m, a, col=column_name: Markup(
                f'<span title="{str(getattr(m, col) or "")[:500]}">'
                f"{truncate_value(getattr(m, col))}</span>"
            )

    return formatters


def redirect_to_referer(view: ModelView, request: Request):
    referer = request.headers.get("Referer")
    if referer:
        return RedirectResponse(referer)
    else:
        return RedirectResponse(request.url_for("admin:list", identity=view.identity))


class OrganizationAdmin(ModelView, model=Organization):
    name = "Organization"
    name_plural = "Organizations"
    icon = "fa-solid fa-building"
    column_list = get_model_column_list(Organization)
    column_searchable_list = get_searchable_columns(Organization)
    column_formatters = create_column_formatters(Organization)
    form_include_pk = True
    page_size = 25
    page_size_options = [25, 50, 100]

    @action(
        name="refresh_subscription",
        label="Refresh Subscription from Stripe",
        confirmation_message="Sync subscription data from Stripe for selected organizations?",
        add_in_detail=True,
        add_in_list=True,
    )
    async def refresh_subscription(self, request: Request) -> RedirectResponse:
        pks = request.query_params.get("pks", "")
        if not pks:
            return redirect_to_referer(self, request)

        pk_list = pks.split(",")
        async with self.session_maker() as session:
            messages = []
            for pk in pk_list:
                try:
                    org_id = UUID(pk)
                    success, message = await sync_subscription_from_stripe(
                        session, org_id
                    )
                    messages.append(f"{pk}: {message}")
                except Exception as e:
                    messages.append(f"{pk}: Error - {str(e)}")

            await session.commit()

        return redirect_to_referer(self, request)


def get_model_icon(model_name: str) -> str:
    """Get appropriate FontAwesome icon for model."""
    icon_map = {
        "User": "fa-solid fa-user",
        "Organization": "fa-solid fa-building",
        "OrganizationMember": "fa-solid fa-users",
        "Namespace": "fa-solid fa-folder",
        "Runtime": "fa-solid fa-server",
        "Workflow": "fa-solid fa-diagram-project",
        "WorkflowDeployment": "fa-solid fa-rocket",
        "WorkflowFolder": "fa-solid fa-folder-tree",
        "IncomingWebhook": "fa-solid fa-blog",
        "Trigger": "fa-solid fa-bolt",
        "Secret": "fa-solid fa-key",
        "Provider": "fa-solid fa-plug",
        "ExecutionHistory": "fa-solid fa-clock-rotate-left",
        "ExecutionLog": "fa-solid fa-scroll",
        "Subscription": "fa-solid fa-credit-card",
        "BillingEvent": "fa-solid fa-receipt",
        "ApiKey": "fa-solid fa-key",
        "DeviceCode": "fa-solid fa-mobile",
        "RefreshToken": "fa-solid fa-rotate",
        "KeyValueTable": "fa-solid fa-table",
        "KeyValueItem": "fa-solid fa-database",
        "KeyValueTablePermission": "fa-solid fa-lock",
        "RecurringTask": "fa-solid fa-repeat",
        "Configuration": "fa-solid fa-gear",
        "AccessTuple": "fa-solid fa-shield",
    }
    return icon_map.get(model_name, "fa-solid fa-table")


def get_default_sort(model_name: str) -> list[tuple[str, bool]]:
    """Get default sort column for a model (column_name, descending)."""
    # Models with created_at should sort by newest first
    sort_map = {
        "ExecutionHistory": [("received_at", True)],
        "ExecutionLog": [("timestamp", True)],
        "BillingEvent": [("created_at", True)],
        "WorkflowDeployment": [("deployed_at", True)],
    }
    return sort_map.get(model_name, [])


def create_model_admin_class(model: Type[Base]) -> Type[ModelView]:
    """Dynamically create a ModelView class for a given model."""
    model_name = model.__name__
    class_name = f"{model_name}Admin"

    # Get config for this model
    searchable_columns = get_searchable_columns(model)
    hidden_in_list = get_columns_to_hide_in_list(model)
    formatters = create_column_formatters(model)
    default_sort = get_default_sort(model_name)

    # Get list columns (exclude hidden ones)
    all_columns = get_model_column_list(model)
    list_columns = [col for col in all_columns if col not in hidden_in_list]

    # Create the class with proper model binding
    class DynamicModelView(ModelView, model=model):
        name = model_name
        name_plural = f"{model_name}s"
        icon = get_model_icon(model_name)
        column_list = list_columns
        column_details_list = all_columns  # Show all columns in detail view
        column_searchable_list = searchable_columns
        column_formatters = formatters
        form_include_pk = True
        page_size = 25
        page_size_options = [25, 50, 100]
        column_default_sort = default_sort if default_sort else None

    # Set the class name for debugging
    DynamicModelView.__name__ = class_name
    DynamicModelView.__qualname__ = class_name

    return DynamicModelView


def get_all_models() -> list[Type[Base]]:
    """Get all SQLAlchemy models from the models module."""
    models = []

    for name, obj in inspect.getmembers(models_module):
        if (
            inspect.isclass(obj)
            and issubclass(obj, Base)
            and obj is not Base
            and hasattr(obj, "__tablename__")
        ):
            models.append(obj)

    return models


# Models that have custom admin classes
CUSTOM_ADMIN_CLASSES: dict[str, Type[ModelView]] = {
    "Organization": OrganizationAdmin,
}


def generate_all_views() -> list[Type[ModelView]]:
    """Generate ModelView classes for all models."""
    models = get_all_models()
    views = []
    for model in models:
        model_name = model.__name__
        if model_name in CUSTOM_ADMIN_CLASSES:
            views.append(CUSTOM_ADMIN_CLASSES[model_name])
        else:
            views.append(create_model_admin_class(model))
    return views


ALL_VIEWS = generate_all_views()
