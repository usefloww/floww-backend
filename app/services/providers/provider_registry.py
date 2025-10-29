from app.services.providers.implementations.gitlab import GitlabProvider
from app.services.providers.implementations.slack import SlackProvider

ALL_PROVIDER_TYPES = [
    GitlabProvider,
    SlackProvider,
]


PROVIDER_TYPES_MAP = {
    provider_type.name: provider_type for provider_type in ALL_PROVIDER_TYPES
}
