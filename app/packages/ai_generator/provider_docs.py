import re
from pathlib import Path
from typing import Dict, Optional

import structlog

logger = structlog.stdlib.get_logger(__name__)


def get_sdk_base_path() -> Path:
    """Get the path to the SDK src directory."""
    backend_dir = Path(__file__).parent.parent.parent
    sdk_dir = backend_dir.parent.parent / "floww-sdk"
    return sdk_dir / "src"


def get_sdk_providers_path() -> Path:
    """
    Get the path to the SDK providers directory.

    Assumes floww-sdk is a sibling directory to floww-backend.
    """
    return get_sdk_base_path() / "providers"


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


def load_common_types() -> Optional[str]:
    """Load the common.ts file containing base types for the SDK."""
    common_file = get_sdk_base_path() / "common.ts"

    if not common_file.exists():
        logger.warning(f"Common types file not found: {common_file}")
        return None

    try:
        content = common_file.read_text(encoding="utf-8")
        logger.debug("Loaded common types")
        return content
    except Exception as e:
        logger.error(f"Error reading common types: {e}")
        return None


def load_secret_documentation() -> Optional[str]:
    """Load the secret.ts file containing the Secret class for custom credentials."""
    secret_file = get_sdk_providers_path() / "secret.ts"

    if not secret_file.exists():
        logger.warning(f"Secret documentation file not found: {secret_file}")
        return None

    try:
        content = secret_file.read_text(encoding="utf-8")
        logger.debug("Loaded secret documentation")
        return content
    except Exception as e:
        logger.error(f"Error reading secret documentation: {e}")
        return None


def load_provider_index() -> Optional[str]:
    """Load the providers index.ts for available provider exports."""
    index_file = get_sdk_providers_path() / "index.ts"

    if not index_file.exists():
        logger.warning(f"Provider index file not found: {index_file}")
        return None

    try:
        content = index_file.read_text(encoding="utf-8")
        logger.debug("Loaded provider index")
        return content
    except Exception as e:
        logger.error(f"Error reading provider index: {e}")
        return None


def extract_provider_capabilities(provider_content: str) -> dict:
    """
    Extract triggers and actions from provider TypeScript content.

    Returns a dict with 'triggers' and 'actions' lists.
    """
    triggers = []
    actions = []

    # Match trigger definitions: onMessage, onPush, etc.
    trigger_pattern = r"^\s*(\w+):\s*\([^)]*\)[^=]*=>\s*(?:WebhookTrigger|CronTrigger|RealtimeTrigger)"
    for match in re.finditer(trigger_pattern, provider_content, re.MULTILINE):
        triggers.append(match.group(1))

    # Match triggers object pattern: triggers = { onMessage: ... }
    triggers_block = re.search(
        r"triggers\s*=\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}", provider_content, re.DOTALL
    )
    if triggers_block:
        trigger_names = re.findall(r"(\w+):\s*\(", triggers_block.group(1))
        triggers.extend(trigger_names)

    # Match action methods in Actions class
    action_pattern = r"async\s+(\w+)\s*\([^)]*\)\s*:\s*Promise"
    for match in re.finditer(action_pattern, provider_content):
        action_name = match.group(1)
        if action_name not in ["getApi", "configure", "initialize"]:
            actions.append(action_name)

    return {
        "triggers": list(set(triggers)),
        "actions": list(set(actions)),
    }


def get_provider_capabilities(provider_name: str) -> Optional[dict]:
    """Get summarized capabilities (triggers/actions) for a provider."""
    content = load_provider_documentation(provider_name)
    if not content:
        return None

    return extract_provider_capabilities(content)
