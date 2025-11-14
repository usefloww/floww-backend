"""GitHub integration for Floww automation platform.

This module provides comprehensive GitHub integration including:
- Webhook triggers for various GitHub events
- Action operations for GitHub API
- OAuth2 authentication support
"""

from typing import TYPE_CHECKING

import httpx
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
)

if TYPE_CHECKING:
    from fastapi import Request

    from app.models import Trigger

logger = structlog.stdlib.get_logger(__name__)


#### Provider ####


class GithubProviderState(BaseModel):
    """GitHub provider authentication state."""

    access_token: str
    server_url: str = "https://api.github.com"


class GithubProvider(ProviderI):
    """GitHub provider for workflow automation.

    Supports OAuth2 authentication and various webhook triggers.
    """

    name: str = "github"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepValue(
            title="GitHub Server URL",
            description="GitHub API server URL (use default for GitHub.com)",
            alias="server_url",
            default="https://api.github.com",
        ),
        ProviderSetupStepSecret(
            title="Access Token",
            description="GitHub Personal Access Token or OAuth2 token",
            alias="access_token",
            placeholder="ghp_xxxxxxxxxxxxxxxxxxxx",
        ),
    ]
    model = GithubProviderState

    async def process_webhook(
        self,
        request: "Request",
        provider_state: BaseModel,
        triggers: list["Trigger"],
    ) -> list["Trigger"]:
        """Process GitHub webhook and return matching triggers.

        Filters events based on:
        - Event type from X-GitHub-Event header
        - Repository owner and name
        - Action type (for events with actions)
        - Additional filters from trigger input
        """
        webhook_data = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )

        # Get the GitHub event type from headers
        event_type = request.headers.get("X-GitHub-Event", "")

        logger.debug(
            "Processing GitHub webhook",
            event_type=event_type,
            trigger_count=len(triggers),
        )

        # Handle ping events (GitHub webhook verification)
        if event_type == "ping":
            logger.info("Received GitHub webhook ping")
            return []

        # Extract common webhook data
        repository = webhook_data.get("repository", {})
        repo_owner = repository.get("owner", {}).get("login")
        repo_name = repository.get("name")
        action = webhook_data.get("action")  # Many events have an action field

        # Map GitHub event types to trigger types
        event_to_trigger_map = {
            "push": "onPush",
            "pull_request": "onPullRequest",
            "issues": "onIssue",
            "issue_comment": "onIssueComment",
            "pull_request_review": "onPullRequestReview",
            "pull_request_review_comment": "onPullRequestReviewComment",
            "release": "onRelease",
            "create": "onCreate",
            "delete": "onDelete",
            "fork": "onFork",
            "star": "onStar",
            "watch": "onWatch",
            "repository": "onRepository",
        }

        expected_trigger_type = event_to_trigger_map.get(event_type)
        if not expected_trigger_type:
            logger.debug(
                "Ignoring unsupported GitHub webhook event",
                event_type=event_type,
            )
            return []

        # Filter triggers based on their input configuration
        matching_triggers = []
        for trigger in triggers:
            # Only process triggers matching this event type
            if trigger.trigger_type != expected_trigger_type:
                continue

            trigger_input = trigger.input or {}

            # Apply repository owner filter if specified
            if trigger_input.get("owner"):
                if repo_owner != trigger_input.get("owner"):
                    logger.debug(
                        "Trigger repository owner filter mismatch",
                        trigger_id=str(trigger.id),
                        expected_owner=trigger_input.get("owner"),
                        actual_owner=repo_owner,
                    )
                    continue

            # Apply repository name filter if specified
            if trigger_input.get("repository"):
                if repo_name != trigger_input.get("repository"):
                    logger.debug(
                        "Trigger repository name filter mismatch",
                        trigger_id=str(trigger.id),
                        expected_repo=trigger_input.get("repository"),
                        actual_repo=repo_name,
                    )
                    continue

            # Apply action filter if specified (for events with actions)
            if trigger_input.get("actions") and action:
                allowed_actions = trigger_input.get("actions", [])
                if action not in allowed_actions:
                    logger.debug(
                        "Trigger action filter mismatch",
                        trigger_id=str(trigger.id),
                        expected_actions=allowed_actions,
                        actual_action=action,
                    )
                    continue

            # This trigger matches!
            matching_triggers.append(trigger)
            logger.info(
                "GitHub event matched trigger",
                trigger_id=str(trigger.id),
                event_type=event_type,
                action=action,
                repository=f"{repo_owner}/{repo_name}",
            )

        return matching_triggers


#### Webhook Triggers ####


class OnPushInput(BaseModel):
    """Input configuration for push event trigger."""

    owner: str
    repository: str
    branch: str | None = None


class OnPushState(BaseModel):
    """State for push event trigger."""

    webhook_id: int
    owner: str
    repository: str
    branch: str | None = None


class OnPush(TriggerI[OnPushInput, OnPushState, GithubProvider]):
    """Trigger for GitHub push events.

    Fires when commits are pushed to a repository.
    """

    async def create(
        self,
        provider: GithubProviderState,
        input: OnPushInput,
        register_webhook,
    ) -> OnPushState:
        """Create a GitHub webhook for push events."""
        webhook_registration = await register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        webhook_data = {
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "insecure_ssl": "0",
            },
            "events": ["push"],
            "active": True,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{provider.server_url}/repos/{input.owner}/{input.repository}/hooks",
                json=webhook_data,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            webhook = response.json()

            return OnPushState(
                webhook_id=webhook["id"],
                owner=input.owner,
                repository=input.repository,
                branch=input.branch,
            )

    async def destroy(
        self,
        provider: GithubProviderState,
        input: OnPushInput,
        state: OnPushState,
    ) -> None:
        """Delete the GitHub webhook."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

    async def refresh(
        self,
        provider: GithubProviderState,
        input: OnPushInput,
        state: OnPushState,
    ) -> OnPushState:
        """Verify webhook still exists and return current state."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return state


class OnPullRequestInput(BaseModel):
    """Input configuration for pull request event trigger."""

    owner: str
    repository: str
    actions: list[str] | None = None  # opened, closed, reopened, synchronize, etc.


class OnPullRequestState(BaseModel):
    """State for pull request event trigger."""

    webhook_id: int
    owner: str
    repository: str
    actions: list[str] | None = None


class OnPullRequest(TriggerI[OnPullRequestInput, OnPullRequestState, GithubProvider]):
    """Trigger for GitHub pull request events.

    Fires when pull requests are opened, closed, merged, etc.
    """

    async def create(
        self,
        provider: GithubProviderState,
        input: OnPullRequestInput,
        register_webhook,
    ) -> OnPullRequestState:
        """Create a GitHub webhook for pull request events."""
        webhook_registration = await register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        webhook_data = {
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "insecure_ssl": "0",
            },
            "events": ["pull_request"],
            "active": True,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{provider.server_url}/repos/{input.owner}/{input.repository}/hooks",
                json=webhook_data,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            webhook = response.json()

            return OnPullRequestState(
                webhook_id=webhook["id"],
                owner=input.owner,
                repository=input.repository,
                actions=input.actions,
            )

    async def destroy(
        self,
        provider: GithubProviderState,
        input: OnPullRequestInput,
        state: OnPullRequestState,
    ) -> None:
        """Delete the GitHub webhook."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

    async def refresh(
        self,
        provider: GithubProviderState,
        input: OnPullRequestInput,
        state: OnPullRequestState,
    ) -> OnPullRequestState:
        """Verify webhook still exists and return current state."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return state


class OnIssueInput(BaseModel):
    """Input configuration for issue event trigger."""

    owner: str
    repository: str
    actions: list[str] | None = None  # opened, closed, edited, etc.


class OnIssueState(BaseModel):
    """State for issue event trigger."""

    webhook_id: int
    owner: str
    repository: str
    actions: list[str] | None = None


class OnIssue(TriggerI[OnIssueInput, OnIssueState, GithubProvider]):
    """Trigger for GitHub issue events.

    Fires when issues are opened, closed, edited, etc.
    """

    async def create(
        self,
        provider: GithubProviderState,
        input: OnIssueInput,
        register_webhook,
    ) -> OnIssueState:
        """Create a GitHub webhook for issue events."""
        webhook_registration = await register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        webhook_data = {
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "insecure_ssl": "0",
            },
            "events": ["issues"],
            "active": True,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{provider.server_url}/repos/{input.owner}/{input.repository}/hooks",
                json=webhook_data,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            webhook = response.json()

            return OnIssueState(
                webhook_id=webhook["id"],
                owner=input.owner,
                repository=input.repository,
                actions=input.actions,
            )

    async def destroy(
        self,
        provider: GithubProviderState,
        input: OnIssueInput,
        state: OnIssueState,
    ) -> None:
        """Delete the GitHub webhook."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

    async def refresh(
        self,
        provider: GithubProviderState,
        input: OnIssueInput,
        state: OnIssueState,
    ) -> OnIssueState:
        """Verify webhook still exists and return current state."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return state


class OnIssueCommentInput(BaseModel):
    """Input configuration for issue comment event trigger."""

    owner: str
    repository: str
    actions: list[str] | None = None  # created, edited, deleted


class OnIssueCommentState(BaseModel):
    """State for issue comment event trigger."""

    webhook_id: int
    owner: str
    repository: str
    actions: list[str] | None = None


class OnIssueComment(
    TriggerI[OnIssueCommentInput, OnIssueCommentState, GithubProvider]
):
    """Trigger for GitHub issue comment events.

    Fires when comments are created, edited, or deleted on issues or pull requests.
    """

    async def create(
        self,
        provider: GithubProviderState,
        input: OnIssueCommentInput,
        register_webhook,
    ) -> OnIssueCommentState:
        """Create a GitHub webhook for issue comment events."""
        webhook_registration = await register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        webhook_data = {
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "insecure_ssl": "0",
            },
            "events": ["issue_comment"],
            "active": True,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{provider.server_url}/repos/{input.owner}/{input.repository}/hooks",
                json=webhook_data,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            webhook = response.json()

            return OnIssueCommentState(
                webhook_id=webhook["id"],
                owner=input.owner,
                repository=input.repository,
                actions=input.actions,
            )

    async def destroy(
        self,
        provider: GithubProviderState,
        input: OnIssueCommentInput,
        state: OnIssueCommentState,
    ) -> None:
        """Delete the GitHub webhook."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

    async def refresh(
        self,
        provider: GithubProviderState,
        input: OnIssueCommentInput,
        state: OnIssueCommentState,
    ) -> OnIssueCommentState:
        """Verify webhook still exists and return current state."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return state


class OnReleaseInput(BaseModel):
    """Input configuration for release event trigger."""

    owner: str
    repository: str
    actions: list[str] | None = None  # published, created, edited, deleted


class OnReleaseState(BaseModel):
    """State for release event trigger."""

    webhook_id: int
    owner: str
    repository: str
    actions: list[str] | None = None


class OnRelease(TriggerI[OnReleaseInput, OnReleaseState, GithubProvider]):
    """Trigger for GitHub release events.

    Fires when releases are published, created, edited, or deleted.
    """

    async def create(
        self,
        provider: GithubProviderState,
        input: OnReleaseInput,
        register_webhook,
    ) -> OnReleaseState:
        """Create a GitHub webhook for release events."""
        webhook_registration = await register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        webhook_data = {
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "insecure_ssl": "0",
            },
            "events": ["release"],
            "active": True,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{provider.server_url}/repos/{input.owner}/{input.repository}/hooks",
                json=webhook_data,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            webhook = response.json()

            return OnReleaseState(
                webhook_id=webhook["id"],
                owner=input.owner,
                repository=input.repository,
                actions=input.actions,
            )

    async def destroy(
        self,
        provider: GithubProviderState,
        input: OnReleaseInput,
        state: OnReleaseState,
    ) -> None:
        """Delete the GitHub webhook."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()

    async def refresh(
        self,
        provider: GithubProviderState,
        input: OnReleaseInput,
        state: OnReleaseState,
    ) -> OnReleaseState:
        """Verify webhook still exists and return current state."""
        headers = {
            "Authorization": f"Bearer {provider.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{provider.server_url}/repos/{state.owner}/{state.repository}/hooks/{state.webhook_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return state


# Registry of GitHub trigger types
GITHUB_TRIGGER_TYPES = {
    "onPush": OnPush,
    "onPullRequest": OnPullRequest,
    "onIssue": OnIssue,
    "onIssueComment": OnIssueComment,
    "onRelease": OnRelease,
}
