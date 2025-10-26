from pydantic import BaseModel

from app.services.providers.provider_setup import (
    ProviderSetupStep,
    ProviderSetupStepSecret,
    ProviderSetupStepValue,
)
from app.services.providers.provider_utils import (
    ProviderI,
    ResourceI,
)


#### Provider ####
class GitlabProviderState(BaseModel):
    url: str
    token: str


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
            alias="token",
            placeholder="glpat-xxxxxxxxxxxxxxxxxxxx",
        ),
    ]
    model = GitlabProviderState


#### Resources ####
class GitlabProjectHookInput(BaseModel):
    pass


class GitlabProjectHookState(BaseModel):
    pass


class GitlabProjectHook(
    ResourceI[GitlabProjectHookInput, GitlabProjectHookState, GitlabProvider]
):
    def create(self, provider, input):
        return GitlabProjectHookState()

    def destroy(self, provider, input, state):
        pass

    def refresh(self, provider, input, state):
        return GitlabProjectHookState()
