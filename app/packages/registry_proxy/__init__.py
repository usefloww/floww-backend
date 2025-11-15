"""Registry proxy package for Docker Registry v2 API.

This package provides generic utilities and provider implementations
for proxying Docker Registry API requests to various backends.
"""

from .providers import (
    DockerRegistryClient,
    ECRRegistryClient,
    RegistryClient,
)
from .proxy import proxy_request, stream_request_body
from .types import RegistryConfig

__all__ = [
    # Protocol
    "RegistryClient",
    # Providers
    "ECRRegistryClient",
    "DockerRegistryClient",
    # Types
    "RegistryConfig",
    # Utilities
    "proxy_request",
    "stream_request_body",
]
