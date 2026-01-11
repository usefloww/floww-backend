from pydantic import BaseModel

from app.services.providers.provider_setup import ProviderSetupStep
from app.services.providers.provider_utils import (
    ProviderI,
    TriggerI,
    TriggerUtils,
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


class OnWebhook(TriggerI[OnWebhookInput, OnWebhookState, BuiltinProviderState]):
    """
    Trigger for incoming webhooks.

    Builtin webhooks are managed entirely by the backend - no external API calls needed.
    The webhook path and method are stored in the state for reference.
    """

    async def create(
        self,
        provider: BuiltinProviderState,
        input: OnWebhookInput,
        utils: TriggerUtils,
    ) -> OnWebhookState:
        """
        Store the webhook configuration.

        Since builtin webhooks are managed internally, no external setup is needed.
        The IncomingWebhook record is created by the TriggerService.
        """
        input_path = input.path.lstrip("/")
        webhook_registration = await utils.register_webhook(
            path=f"/webhook/{str(utils.trigger.workflow_id)}/{input_path}",
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
        utils: TriggerUtils,
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


class OnCron(TriggerI[OnCronInput, OnCronState, BuiltinProviderState]):
    """
    Trigger for cron schedules.

    Builtin cron triggers are managed entirely by the backend - no external API calls needed.
    The cron expression is stored in the state for scheduling.
    """

    async def create(
        self,
        provider: BuiltinProviderState,
        input: OnCronInput,
        utils: TriggerUtils,
    ) -> OnCronState:
        """
        Store the cron configuration.

        Since builtin cron triggers are managed internally, no external setup is needed.
        The scheduler uses the expression from the trigger state.
        """
        # Register recurring task for cron scheduling
        await utils.register_recurring_task(
            cron_expression=input.expression,
        )

        return OnCronState(
            expression=input.expression,
        )

    async def destroy(
        self,
        provider: BuiltinProviderState,
        input: OnCronInput,
        state: OnCronState,
        utils: TriggerUtils,
    ) -> None:
        """
        Clean up the trigger state.

        Removes the recurring task from both APScheduler and the database.
        """
        await utils.unregister_recurring_task()

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


class OnManualInput(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict | None = None


class OnManualState(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict | None = None


class OnManual(TriggerI[OnManualInput, OnManualState, BuiltinProviderState]):
    """
    Trigger for manual invocation via UI or API.

    Manual triggers can be invoked on-demand by users through the dashboard or API.
    They support custom input schemas (JSON Schema) for runtime parameters.
    """

    async def create(
        self,
        provider: BuiltinProviderState,
        input: OnManualInput,
        utils: TriggerUtils,
    ) -> OnManualState:
        """
        Store the manual trigger configuration.

        No external setup needed - manual triggers are invoked directly via API.
        The input schema is stored for validation during invocation.
        """
        return OnManualState(
            name=input.name,
            description=input.description,
            input_schema=input.input_schema,
        )

    async def destroy(
        self,
        provider: BuiltinProviderState,
        input: OnManualInput,
        state: OnManualState,
        utils: TriggerUtils,
    ) -> None:
        """
        Clean up the trigger state.

        No external cleanup needed for manual triggers.
        """
        pass

    async def refresh(
        self,
        provider: BuiltinProviderState,
        input: OnManualInput,
        state: OnManualState,
    ) -> OnManualState:
        """
        Verify the trigger state is still valid.

        Manual triggers are always valid as they have no external dependencies.
        """
        return state


# Registry of builtin trigger types
BUILTIN_TRIGGER_TYPES = {
    "onWebhook": OnWebhook,
    "onCron": OnCron,
    "onManual": OnManual,
}
