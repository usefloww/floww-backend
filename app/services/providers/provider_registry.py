from app.services.providers.ai_anthropic import AnthropicProvider
from app.services.providers.ai_google import GoogleAIProvider
from app.services.providers.ai_openai import OpenAIProvider
from app.services.providers.implementations.builtin import BuiltinProvider
from app.services.providers.implementations.discord import DiscordProvider
from app.services.providers.implementations.github import GithubProvider
from app.services.providers.implementations.gitlab import GitlabProvider
from app.services.providers.implementations.google_calendar import (
    GoogleCalendarProvider,
)
from app.services.providers.implementations.jira import JiraProvider
from app.services.providers.implementations.kvstore import KVStoreProvider
from app.services.providers.implementations.slack import SlackProvider
from app.services.providers.implementations.todoist import TodoistProvider

ALL_PROVIDER_TYPES = [
    BuiltinProvider,
    DiscordProvider,
    GithubProvider,
    GitlabProvider,
    GoogleCalendarProvider,
    JiraProvider,
    KVStoreProvider,
    SlackProvider,
    TodoistProvider,
    OpenAIProvider,
    AnthropicProvider,
    GoogleAIProvider,
]


PROVIDER_TYPES_MAP = {
    provider_type.name: provider_type for provider_type in ALL_PROVIDER_TYPES
}
