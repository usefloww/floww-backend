from pathlib import Path
from typing import Dict, Optional

import structlog

logger = structlog.stdlib.get_logger(__name__)


def get_sdk_providers_path() -> Path:
    """
    Get the path to the SDK providers directory.

    Assumes floww-sdk is a sibling directory to floww-backend.
    """
    # Get the backend directory (where this file is located)
    backend_dir = Path(__file__).parent.parent.parent
    # Go up one level and into floww-sdk
    sdk_dir = backend_dir.parent.parent / "floww-sdk"
    providers_path = sdk_dir / "src" / "providers"
    return providers_path


def load_provider_documentation(provider_name: str) -> Optional[str]:
    """
    Load documentation for a single provider from the SDK.

    Args:
        provider_name: Name of the provider (e.g., "slack", "jira")

    Returns:
        Raw file content as string, or None if file not found
    """
    provider_name = provider_name.lower()
    providers_path = get_sdk_providers_path()
    provider_file = providers_path / f"{provider_name}.ts"

    if not provider_file.exists():
        logger.warning(f"Provider documentation file not found: {provider_file}")
        return None

    try:
        content = provider_file.read_text(encoding="utf-8")
        logger.debug(f"Loaded provider documentation for {provider_name}")
        return content
    except Exception as e:
        logger.error(f"Error reading provider documentation for {provider_name}: {e}")
        return None


def load_provider_documentation_batch(provider_names: list[str]) -> Dict[str, str]:
    """
    Load documentation for multiple providers.

    Args:
        provider_names: List of provider names to load

    Returns:
        Dictionary mapping provider name to file content.
        Only includes providers where documentation was successfully loaded.
    """
    docs = {}
    for provider_name in provider_names:
        content = load_provider_documentation(provider_name)
        if content:
            docs[provider_name] = content

    return docs
