from app.services.providers.gitlab import GitlabProvider

ALL_PROVIDER_TYPES = [
    GitlabProvider,
]


PROVIDER_TYPES_MAP = {
    provider_type.name: provider_type for provider_type in ALL_PROVIDER_TYPES
}
