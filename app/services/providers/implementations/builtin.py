from pydantic import BaseModel

from app.services.providers.provider_setup import ProviderSetupStep
from app.services.providers.provider_utils import (
    ProviderI,
    TriggerI,
)


#### Provider ####
class BuiltinProviderState(BaseModel):
    """Builtin provider has no configuration."""

    pass


class BuiltinProvider(ProviderI):
    name: str = "builtin"
    setup_steps: list[ProviderSetupStep] = []
    model = BuiltinProviderState


#### Triggers ####
class OnWebhookInput(BaseModel):
    path: str
    method: str = "POST"


class OnWebhookState(BaseModel):
    path: str
    method: str
    webhook_url: str


class OnWebhook(TriggerI[OnWebhookInput, OnWebhookState, BuiltinProvider]):
    """
    Trigger for incoming webhooks.

    Builtin webhooks are managed entirely by the backend - no external API calls needed.
    The webhook path and method are stored in the state for reference.
    """

    async def create(
        self,
        provider: BuiltinProviderState,
        input: OnWebhookInput,
        register_webhook,
    ) -> OnWebhookState:
        """
        Store the webhook configuration.

        Since builtin webhooks are managed internally, no external setup is needed.
        The IncomingWebhook record is created by the TriggerService.
        """
        webhook_registration = await register_webhook(
            path=input.path,
            method=input.method,
        )
        return OnWebhookState(
            path=webhook_registration["path"],
            method=webhook_registration["method"],
            webhook_url=webhook_registration["url"],
        )

    async def destroy(
        self,
        provider: BuiltinProviderState,
        input: OnWebhookInput,
        state: OnWebhookState,
    ) -> None:
        """
        Clean up the trigger state.

        Since builtin webhooks are managed internally, no external cleanup is needed.
        The IncomingWebhook record is deleted by the TriggerService.
        """
        pass

    async def refresh(
        self,
        provider: BuiltinProviderState,
        input: OnWebhookInput,
        state: OnWebhookState,
    ) -> OnWebhookState:
        """
        Verify the trigger state is still valid.

        Since builtin webhooks are managed internally, we just return the existing state.
        """
        return state


class OnCronInput(BaseModel):
    expression: str


class OnCronState(BaseModel):
    expression: str


class OnCron(TriggerI[OnCronInput, OnCronState, BuiltinProvider]):
    """
    Trigger for cron schedules.

    Builtin cron triggers are managed entirely by the backend - no external API calls needed.
    The cron expression is stored in the state for scheduling.
    """

    async def create(
        self,
        provider: BuiltinProviderState,
        input: OnCronInput,
        register_webhook,
    ) -> OnCronState:
        """
        Store the cron configuration.

        Since builtin cron triggers are managed internally, no external setup is needed.
        The scheduler uses the expression from the trigger state.
        """
        return OnCronState(
            expression=input.expression,
        )

    async def destroy(
        self,
        provider: BuiltinProviderState,
        input: OnCronInput,
        state: OnCronState,
    ) -> None:
        """
        Clean up the trigger state.

        Since builtin cron triggers are managed internally, no external cleanup is needed.
        The scheduler will stop using this trigger when it's deleted.
        """
        pass

    async def refresh(
        self,
        provider: BuiltinProviderState,
        input: OnCronInput,
        state: OnCronState,
    ) -> OnCronState:
        """
        Verify the trigger state is still valid.

        Since builtin cron triggers are managed internally, we just return the existing state.
        """
        return state


# Registry of builtin trigger types
BUILTIN_TRIGGER_TYPES = {
    "onWebhook": OnWebhook,
    "onCron": OnCron,
}
