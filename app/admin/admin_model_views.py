import inspect
from typing import Type

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqladmin import ModelView

import app.models as models_module
from app.models import Base


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
            if hasattr(column.type, "python_type") and column.type.python_type == str:
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


def get_model_icon(model_name: str) -> str:
    """Get appropriate FontAwesome icon for model."""
    icon_map = {
        "User": "fa-solid fa-user",
        "Organization": "fa-solid fa-building",
        "OrganizationMember": "fa-solid fa-users",
        "Namespace": "fa-solid fa-folder",
        "NamespaceMember": "fa-solid fa-user-group",
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


def generate_all_views() -> list[Type[ModelView]]:
    """Generate ModelView classes for all models."""
    models = get_all_models()
    return [create_model_admin_class(model) for model in models]


ALL_VIEWS = generate_all_views()
