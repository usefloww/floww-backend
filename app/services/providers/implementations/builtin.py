from croniter import croniter
from pydantic import BaseModel, field_validator

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

    @field_validator("expression")
    def validate_expression(cls, v: str) -> str:
        if not croniter.is_valid(v):
            if croniter.is_valid(v, second_at_beginning=True):
                raise ValueError(
                    "Seconds field is only supported locally, not for deployed workflows."
                )
            raise ValueError(f"Invalid cron expression: {v}")
        return v


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


# Registry of builtin trigger types
BUILTIN_TRIGGER_TYPES = {
    "onWebhook": OnWebhook,
    "onCron": OnCron,
}
