import httpx
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


#### Provider ####
class GitlabProviderState(BaseModel):
    url: str
    accessToken: str


class GitlabProvider(ProviderI):
    name: str = "gitlab"
    setup_steps: list[ProviderSetupStep] = [
        ProviderSetupStepValue(
            title="Instance URL",
            description="GitLab base URL",
            alias="url",
            default="https://gitlab.com",
        ),
        ProviderSetupStepSecret(
            title="Access Token",
            description="Personal access token",
            alias="accessToken",
            placeholder="glpat-xxxxxxxxxxxxxxxxxxxx",
        ),
    ]
    model = GitlabProviderState


#### Triggers ####
class OnMergeRequestCommentInput(BaseModel):
    projectId: str | None = None
    groupId: str | None = None


class OnMergeRequestCommentState(BaseModel):
    webhook_id: int
    project_id: str | None = None
    group_id: str | None = None


class OnMergeRequestComment(
    TriggerI[
        OnMergeRequestCommentInput, OnMergeRequestCommentState, GitlabProviderState
    ]
):
    async def create(
        self,
        provider: GitlabProviderState,
        input: OnMergeRequestCommentInput,
        utils: TriggerUtils,
    ) -> OnMergeRequestCommentState:
        """Create a GitLab webhook for merge request comments."""
        webhook_registration = await utils.register_webhook(method="POST")
        webhook_url = webhook_registration["url"]

        headers = {
            "PRIVATE-TOKEN": provider.accessToken,
            "Content-Type": "application/json",
        }

        webhook_data = {
            "url": webhook_url,
            "note_events": True,
            "merge_requests_events": True,
            "push_events": False,
            "issues_events": False,
        }

        async with httpx.AsyncClient() as client:
            if input.projectId:
                # Create project webhook
                response = await client.post(
                    f"{provider.url}/api/v4/projects/{input.projectId}/hooks",
                    json=webhook_data,
                    headers=headers,
                )
                response.raise_for_status()
                webhook = response.json()
                return OnMergeRequestCommentState(
                    webhook_id=webhook["id"],
                    project_id=input.projectId,
                )
            elif input.groupId:
                # Create group webhook
                response = await client.post(
                    f"{provider.url}/api/v4/groups/{input.groupId}/hooks",
                    json=webhook_data,
                    headers=headers,
                )
                response.raise_for_status()
                webhook = response.json()
                return OnMergeRequestCommentState(
                    webhook_id=webhook["id"],
                    group_id=input.groupId,
                )
            else:
                raise ValueError("Either projectId or groupId must be provided")

    async def destroy(
        self,
        provider: GitlabProviderState,
        input: OnMergeRequestCommentInput,
        state: OnMergeRequestCommentState,
    ) -> None:
        """Delete a GitLab webhook."""
        headers = {
            "PRIVATE-TOKEN": provider.accessToken,
        }

        async with httpx.AsyncClient() as client:
            if state.project_id:
                # Delete project webhook
                response = await client.delete(
                    f"{provider.url}/api/v4/projects/{state.project_id}/hooks/{state.webhook_id}",
                    headers=headers,
                )
                response.raise_for_status()
            elif state.group_id:
                # Delete group webhook
                response = await client.delete(
                    f"{provider.url}/api/v4/groups/{state.group_id}/hooks/{state.webhook_id}",
                    headers=headers,
                )
                response.raise_for_status()

    async def refresh(
        self,
        provider: GitlabProviderState,
        input: OnMergeRequestCommentInput,
        state: OnMergeRequestCommentState,
    ) -> OnMergeRequestCommentState:
        """Verify webhook still exists and return current state."""
        headers = {
            "PRIVATE-TOKEN": provider.accessToken,
        }

        async with httpx.AsyncClient() as client:
            if state.project_id:
                # Get project webhook
                response = await client.get(
                    f"{provider.url}/api/v4/projects/{state.project_id}/hooks/{state.webhook_id}",
                    headers=headers,
                )
                response.raise_for_status()
                return state
            elif state.group_id:
                # Get group webhook
                response = await client.get(
                    f"{provider.url}/api/v4/groups/{state.group_id}/hooks/{state.webhook_id}",
                    headers=headers,
                )
                response.raise_for_status()
                return state
            else:
                raise ValueError("Either project_id or group_id must be set in state")


# Registry of GitLab trigger types
GITLAB_TRIGGER_TYPES = {
    "onMergeRequestComment": OnMergeRequestComment,
}
