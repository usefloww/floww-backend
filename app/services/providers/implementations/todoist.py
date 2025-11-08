from typing import TYPE_CHECKING
import structlog
from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
)
from app.services.providers.provider_utils import ProviderI

if TYPE_CHECKING:
    from fastapi import Request

    from app.models import Trigger

logger = structlog.stdlib.get_logger(__name__)


#### Provider ####
class TodoistProviderState(BaseModel):
    api_token: str


class TodoistProvider(ProviderI):
    """
    Todoist Provider

    Provides integration with Todoist task management API.
    Authentication is via API token.
    """

    name: str = "todoist"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepSecret(
            title="API Token",
            description="Your Todoist API token (create at https://todoist.com/prefs/integrations)",
            alias="api_token",
            placeholder="xxxxxxxxxxxxxxxxxxxx",
        ),
    ]
    model = TodoistProviderState

    async def process_webhook(
        self,
        request: "Request",
        provider_state: BaseModel,
        triggers: list["Trigger"],
    ) -> list["Trigger"]:
        """
        Process Todoist webhook and return list of triggers that should be executed.

        Note: This is a placeholder implementation as triggers are not yet supported.
        Todoist webhooks will be implemented in a future version.
        """
        logger.debug(
            "Todoist webhook received (triggers not yet implemented)",
            trigger_count=len(triggers),
        )
        return []


# Registry of Todoist trigger types (empty for now)
TODOIST_TRIGGER_TYPES = {}
