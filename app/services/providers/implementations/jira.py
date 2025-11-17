from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
    ProviderSetupStepValue,
)
from app.services.providers.provider_utils import (
    ProviderI,
    TriggerI,
    TriggerUtils,
)

if TYPE_CHECKING:
    from fastapi import Request

    from app.models import Trigger

logger = structlog.stdlib.get_logger(__name__)


#### Provider ####
class JiraProviderState(BaseModel):
    instance_url: str
    email: str
    api_token: str


class JiraProvider(ProviderI):
    name: str = "jira"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepValue(
            title="Instance URL",
            description="Your Jira Cloud instance URL",
            alias="instance_url",
            placeholder="https://your-domain.atlassian.net",
        ),
        ProviderSetupStepValue(
            title="Email",
            description="Jira account email for authentication",
            alias="email",
            placeholder="user@example.com",
        ),
        ProviderSetupStepSecret(
            title="API Token",
            description="Jira API token (create at https://id.atlassian.com/manage-profile/security/api-tokens)",
            alias="api_token",
            placeholder="xxxxxxxxxxxxxxxxxxxx",
        ),
    ]
    model = JiraProviderState

    async def process_webhook(
        self,
        request: "Request",
        provider_state: BaseModel,
        triggers: list["Trigger"],
    ) -> list["Trigger"]:
        """
        Process Jira webhook and return list of triggers that should be executed.

        Filters events based on:
        - Webhook event type (issue_created, issue_updated, comment_created)
        - Project key (if specified in trigger input)
        - Issue type (if specified in trigger input)
        """
        webhook_data = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )

        webhook_event = webhook_data.get("webhookEvent", "")
        logger.debug(
            "Processing Jira webhook",
            webhook_event=webhook_event,
            trigger_count=len(triggers),
        )

        # Extract issue and project information
        issue = webhook_data.get("issue", {})
        issue_fields = issue.get("fields", {})
        project = issue_fields.get("project", {})
        project_key = project.get("key")
        issue_type = issue_fields.get("issuetype", {}).get("name")

        # Map webhook event to trigger type
        event_to_trigger_map = {
            "jira:issue_created": "onIssueCreated",
            "jira:issue_updated": "onIssueUpdated",
            "comment_created": "onCommentAdded",
        }

        expected_trigger_type = event_to_trigger_map.get(webhook_event)
        if not expected_trigger_type:
            logger.debug(
                "Ignoring unknown Jira webhook event",
                webhook_event=webhook_event,
            )
            return []

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process triggers matching this event type
            if trigger.trigger_type != expected_trigger_type:
                logger.debug(
                    "Trigger type mismatch",
                    trigger_id=str(trigger.id),
                    expected=expected_trigger_type,
                    actual=trigger.trigger_type,
                )
                continue

            trigger_input = trigger.input or {}

            # Apply project key filter if specified
            if trigger_input.get("project_key"):
                if project_key != trigger_input.get("project_key"):
                    logger.debug(
                        "Trigger project filter mismatch",
                        trigger_id=str(trigger.id),
                        expected_project=trigger_input.get("project_key"),
                        actual_project=project_key,
                    )
                    continue

            # Apply issue type filter if specified (only for issue events)
            if expected_trigger_type in [
                "onIssueCreated",
                "onIssueUpdated",
            ] and trigger_input.get("issue_type"):
                if issue_type != trigger_input.get("issue_type"):
                    logger.debug(
                        "Trigger issue type filter mismatch",
                        trigger_id=str(trigger.id),
                        expected_issue_type=trigger_input.get("issue_type"),
                        actual_issue_type=issue_type,
                    )
                    continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "Jira event matched trigger",
                trigger_id=str(trigger.id),
                webhook_event=webhook_event,
                project_key=project_key,
                issue_type=issue_type,
            )

        return matching_triggers


#### Triggers ####


class OnIssueCreatedInput(BaseModel):
    project_key: str | None = None
    issue_type: str | None = None


class OnIssueCreatedState(BaseModel):
    webhook_url: str
    project_key: str | None = None
    issue_type: str | None = None
    jql_filter: str | None = None


class OnIssueCreated(
    TriggerI[OnIssueCreatedInput, OnIssueCreatedState, JiraProviderState]
):
    """
    Trigger for Jira issue created events.

    Jira only allows REST-based webhook management for apps. Since Flows authenticates
    with a user API token, the webhook must be created manually inside the Jira
    administration UI. The generated webhook_url and JQL filter help complete the setup.
    """

    async def create(
        self,
        provider: JiraProviderState,
        input: OnIssueCreatedInput,
        utils: TriggerUtils,
    ) -> OnIssueCreatedState:
        """Prepare webhook registration details for Jira issue created events."""
        webhook_registration = await utils.register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        jql_conditions = []
        if input.project_key:
            jql_conditions.append(f"project = {input.project_key}")
        if input.issue_type:
            jql_conditions.append(f'issuetype = "{input.issue_type}"')

        jql_filter = " AND ".join(jql_conditions) if jql_conditions else None

        return OnIssueCreatedState(
            webhook_url=webhook_url,
            project_key=input.project_key,
            issue_type=input.issue_type,
            jql_filter=jql_filter,
        )

    async def destroy(
        self,
        provider: JiraProviderState,
        input: OnIssueCreatedInput,
        state: OnIssueCreatedState,
        utils: TriggerUtils,
    ) -> None:
        """No API cleanup required - webhook must be removed manually in Jira."""
        pass

    async def refresh(
        self,
        provider: JiraProviderState,
        input: OnIssueCreatedInput,
        state: OnIssueCreatedState,
    ) -> OnIssueCreatedState:
        """
        We cannot verify webhook status via the API because it was created manually.

        Returns the stored state so the UI can continue to display the webhook URL and
        associated filters.
        """
        return state


class OnIssueUpdatedInput(BaseModel):
    project_key: str | None = None
    issue_type: str | None = None


class OnIssueUpdatedState(BaseModel):
    webhook_url: str
    project_key: str | None = None
    issue_type: str | None = None
    jql_filter: str | None = None


class OnIssueUpdated(
    TriggerI[OnIssueUpdatedInput, OnIssueUpdatedState, JiraProviderState]
):
    """
    Trigger for Jira issue updated events.

    Jira webhooks for updates must be configured manually via the Jira UI unless
    registered by an installed app. We return the webhook URL and filters so the
    user can complete the setup.
    """

    async def create(
        self,
        provider: JiraProviderState,
        input: OnIssueUpdatedInput,
        utils: TriggerUtils,
    ) -> OnIssueUpdatedState:
        """Prepare webhook registration details for Jira issue updated events."""
        webhook_registration = await utils.register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        jql_conditions = []
        if input.project_key:
            jql_conditions.append(f"project = {input.project_key}")
        if input.issue_type:
            jql_conditions.append(f'issuetype = "{input.issue_type}"')

        jql_filter = " AND ".join(jql_conditions) if jql_conditions else None

        return OnIssueUpdatedState(
            webhook_url=webhook_url,
            project_key=input.project_key,
            issue_type=input.issue_type,
            jql_filter=jql_filter,
        )

    async def destroy(
        self,
        provider: JiraProviderState,
        input: OnIssueUpdatedInput,
        state: OnIssueUpdatedState,
        utils: TriggerUtils,
    ) -> None:
        """No API cleanup required - webhook must be removed manually in Jira."""
        pass

    async def refresh(
        self,
        provider: JiraProviderState,
        input: OnIssueUpdatedInput,
        state: OnIssueUpdatedState,
    ) -> OnIssueUpdatedState:
        """Return stored state; manual webhooks cannot be programmatically verified."""
        return state


class OnCommentAddedInput(BaseModel):
    project_key: str | None = None


class OnCommentAddedState(BaseModel):
    webhook_url: str
    project_key: str | None = None
    jql_filter: str | None = None


class OnCommentAdded(
    TriggerI[OnCommentAddedInput, OnCommentAddedState, JiraProviderState]
):
    """
    Trigger for Jira comment added events.

    Jira comment notifications must be configured manually in the Jira UI. We surface
    the webhook URL so the user can enable the Comment created event.
    """

    async def create(
        self,
        provider: JiraProviderState,
        input: OnCommentAddedInput,
        utils: TriggerUtils,
    ) -> OnCommentAddedState:
        """Prepare webhook registration details for Jira comment added events."""
        webhook_registration = await utils.register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        jql_filter = None
        if input.project_key:
            jql_filter = f"project = {input.project_key}"

        return OnCommentAddedState(
            webhook_url=webhook_url,
            project_key=input.project_key,
            jql_filter=jql_filter,
        )

    async def destroy(
        self,
        provider: JiraProviderState,
        input: OnCommentAddedInput,
        state: OnCommentAddedState,
        utils: TriggerUtils,
    ) -> None:
        """No API cleanup required - webhook must be removed manually in Jira."""
        pass

    async def refresh(
        self,
        provider: JiraProviderState,
        input: OnCommentAddedInput,
        state: OnCommentAddedState,
    ) -> OnCommentAddedState:
        """Return stored state; manual webhooks cannot be programmatically verified."""
        return state


# Registry of Jira trigger types
JIRA_TRIGGER_TYPES = {
    "onIssueCreated": OnIssueCreated,
    "onIssueUpdated": OnIssueUpdated,
    "onCommentAdded": OnCommentAdded,
}
