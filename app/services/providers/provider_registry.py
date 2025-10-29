from app.services.providers.implementations.gitlab import GitlabProvider
from app.services.providers.implementations.slack import SlackProvider
from app.services.providers.ai_openai import OpenAIProvider
from app.services.providers.ai_anthropic import AnthropicProvider
from app.services.providers.ai_google import GoogleAIProvider

ALL_PROVIDER_TYPES = [
    GitlabProvider,
    SlackProvider,
    OpenAIProvider,
    AnthropicProvider,
    GoogleAIProvider,
]


PROVIDER_TYPES_MAP = {
    provider_type.name: provider_type for provider_type in ALL_PROVIDER_TYPES
}
