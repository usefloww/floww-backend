import inspect
from typing import Type
from uuid import UUID

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqladmin import ModelView, action

import app.models as models_module
from app.models import Base, Organization
from app.services.billing_service import sync_subscription_from_stripe


def get_model_column_list(model: Type[Base]):
    return [column.name for column in model.__table__.columns]


def get_searchable_columns(model: Type[Base]) -> list[str]:
    """Get columns that should be searchable (fields ending in _id or containing 'name')."""
    searchable = []
    for column in model.__table__.columns:
        column_name = column.name
        # Include fields ending in _id or containing 'name'
        if column_name.endswith("_id") or "name" in column_name.lower():
            # Only include string-based columns for search
            if hasattr(column.type, "python_type") and column.type.python_type is str:
                searchable.append(column_name)
            # Also include String columns without python_type check
            elif str(column.type).startswith("VARCHAR") or str(column.type).startswith(
                "TEXT"
            ):
                searchable.append(column_name)
    return searchable


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
    form_include_pk = True

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
        "IncomingWebhook": "fa-solid fa-blog",
    }
    return icon_map.get(model_name, "fa-solid fa-table")


def create_model_admin_class(model: Type[Base]) -> Type[ModelView]:
    """Dynamically create a ModelView class for a given model."""
    model_name = model.__name__
    class_name = f"{model_name}Admin"

    # Get searchable columns for this model
    searchable_columns = get_searchable_columns(model)

    # Create the class with proper model binding
    class DynamicModelView(ModelView, model=model):
        name = model_name
        name_plural = f"{model_name}s"
        icon = get_model_icon(model_name)
        column_list = get_model_column_list(model)
        column_searchable_list = searchable_columns
        form_include_pk = True

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
