from typing import List, Optional, Tuple

from app.services.providers.provider_registry import ALL_PROVIDER_TYPES

# Platform alias mapping for fuzzy matching
# Maps common user-friendly names to provider names
PLATFORM_ALIASES = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "pg": "postgresql",
    "salesforce": "salesforce",
    "sf": "salesforce",
    "slack": "slack",
    "jira": "jira",
    "gitlab": "gitlab",
    "github": "github",
    "discord": "discord",
    "todoist": "todoist",
    "kvstore": "kvstore",
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "googleai": "google",
}


def get_available_provider_names() -> List[str]:
    """Get list of available provider names from the registry."""
    return [provider.name for provider in ALL_PROVIDER_TYPES]


def normalize_platform_name(platform: str) -> str:
    """Normalize platform name to lowercase."""
    return platform.lower().strip()


def match_platform(
    platform: str, available_providers: List[str]
) -> Tuple[bool, Optional[str]]:
    """
    Match a platform name against available providers.

    Returns:
        Tuple of (found, matched_provider_name)
        - found: True if a match was found
        - matched_provider_name: The matched provider name, or None if not found
    """
    normalized = normalize_platform_name(platform)
    available_normalized = {normalize_platform_name(p): p for p in available_providers}

    # Try exact match first
    if normalized in available_normalized:
        return True, available_normalized[normalized]

    # Try alias mapping
    if normalized in PLATFORM_ALIASES:
        alias_target = PLATFORM_ALIASES[normalized]
        if alias_target in available_normalized:
            return True, available_normalized[alias_target]

    # Try fuzzy matching (check if normalized platform is contained in any provider name or vice versa)
    for provider_normalized, provider_original in available_normalized.items():
        if normalized in provider_normalized or provider_normalized in normalized:
            return True, provider_original

    return False, None


def validate_platforms(platforms: List[str]) -> Tuple[List[str], List[str]]:
    """
    Validate platforms against available providers.

    Args:
        platforms: List of platform names to validate

    Returns:
        Tuple of (matched_providers, missing_providers)
        - matched_providers: List of unique provider names that were matched
        - missing_providers: List of platform names that couldn't be matched
    """
    available_providers = get_available_provider_names()
    matched = []
    missing = []

    for platform in platforms:
        found, matched_name = match_platform(platform, available_providers)
        if found:
            # Deduplicate matched providers
            if matched_name not in matched:
                matched.append(matched_name)
        else:
            missing.append(platform)

    return matched, missing
