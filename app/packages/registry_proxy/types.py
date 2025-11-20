"""Registry proxy types and data structures.

This module contains shared types used across the registry proxy package.
No dependencies on app.* modules to maintain independence.
"""

from dataclasses import dataclass


@dataclass
class RegistryConfig:
    """Configuration for a Docker registry.

    Attributes:
        registry_url: The base URL of the registry (e.g., "http://registry:5000" or
                     "501046919403.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda")
        public_api_url: The public URL of our backend API for rewriting Location headers
                       (e.g., "https://app.usefloww.dev" or "http://localhost:8000")
    """

    registry_url: str
    public_api_url: str
