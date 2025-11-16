"""Registry client providers for different registry types.

This module provides the RegistryClient protocol and concrete implementations
for different registry backends (ECR, Docker Registry, Kubernetes).
"""

from typing import Any, Optional, Protocol

import structlog
from botocore.exceptions import ClientError
from fastapi import Request
from fastapi.responses import StreamingResponse

from .proxy import proxy_request
from .types import RegistryConfig

logger = structlog.stdlib.get_logger(__name__)


class RegistryClient(Protocol):
    """Protocol for registry client implementations.

    All registry clients must implement the proxy method to handle
    Docker Registry v2 API requests.
    """

    async def proxy(
        self,
        request: Request,
        repository: str,
        path_suffix: str,
    ) -> StreamingResponse:
        """Proxy a Docker Registry request to the backend registry.

        Args:
            request: Original FastAPI request from Docker client
            repository: Docker repository name (e.g., "trigger-lambda")
            path_suffix: Remaining path after repository (e.g., "/manifests/latest")

        Returns:
            StreamingResponse with the registry's response

        Raises:
            Exception if proxying fails
        """
        ...

    async def get_image_digest(self, tag: str) -> str | None:
        """Get image digest for a given tag.

        Args:
            tag: Image tag/hash to look up

        Returns:
            Image digest (e.g., "sha256:abc...") or None if not found

        Raises:
            Exception if the operation fails unexpectedly
        """
        ...


class ECRRegistryClient:
    """AWS ECR registry client.

    Handles authentication and proxying for AWS Elastic Container Registry.
    """

    def __init__(
        self,
        config: RegistryConfig,
        ecr_client: Any,
        repository_name: str,
    ):
        """Initialize ECR registry client.

        Args:
            config: Registry configuration (URLs)
            ecr_client: Boto3 ECR client instance
            repository_name: ECR repository name (e.g., "trigger-lambda")
        """
        self.config = config
        self.ecr_client = ecr_client
        self.repository_name = repository_name
        self._token_cache: Optional[dict] = None

    def _get_authorization_token(self) -> str:
        """Get ECR authorization token for proxying requests.

        Returns the base64-encoded username:password for ECR authentication.
        Tokens are cached and refreshed automatically.

        Returns:
            Base64-encoded ECR authorization token

        Raises:
            Exception if unable to get ECR token
        """
        # TODO: Implement caching with expiry check
        # For now, always fetch fresh token (ECR tokens are valid for 12 hours)

        try:
            response = self.ecr_client.get_authorization_token()

            if not response.get("authorizationData"):
                raise ValueError("No authorization data in ECR response")

            auth_data = response["authorizationData"][0]
            token = auth_data["authorizationToken"]

            logger.debug("Retrieved ECR authorization token")
            return token

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error(
                "Failed to get ECR authorization token",
                error_code=error_code,
                error_message=str(e),
            )
            raise
        except Exception as e:
            logger.error("Unexpected error getting ECR token", error=str(e))
            raise

    def _get_registry_host(self) -> str:
        """Extract registry hostname from ECR_REGISTRY_URL.

        Returns:
            Registry hostname (e.g., "501046919403.dkr.ecr.us-east-1.amazonaws.com")
        """
        registry_with_repo = self.config.registry_url
        if "/" in registry_with_repo:
            return registry_with_repo.split("/")[0]
        return registry_with_repo

    async def proxy(
        self,
        request: Request,
        repository: str,
        path_suffix: str,
    ) -> StreamingResponse:
        """Proxy a Docker Registry request to AWS ECR.

        Args:
            request: Original FastAPI request from Docker client
            repository: Docker repository name (e.g., "trigger-lambda")
            path_suffix: Remaining path after repository (e.g., "/manifests/latest")

        Returns:
            StreamingResponse with ECR's response

        Raises:
            Exception if proxying fails
        """
        # Get ECR authorization token
        try:
            ecr_token = self._get_authorization_token()
        except Exception as e:
            logger.error("Failed to get ECR token for proxy request", error=str(e))
            raise

        # Build ECR target URL
        registry_host = self._get_registry_host()
        target_url = f"https://{registry_host}/v2/{repository}{path_suffix}"

        logger.info(
            "Proxying request to ECR",
            method=request.method,
            target_url=target_url,
            repository=repository,
        )

        # Use the generic proxy with ECR-specific auth
        return await proxy_request(
            request=request,
            target_url=target_url,
            auth_header=f"Basic {ecr_token}",
            registry_host=registry_host,
            proxy_url=self.config.public_api_url,
        )

    async def get_image_digest(self, tag: str) -> str | None:
        """Get image digest for a given tag from ECR.

        Args:
            tag: Image tag/hash to look up

        Returns:
            Image digest (e.g., "sha256:abc...") or None if not found

        Raises:
            Exception if the operation fails unexpectedly
        """
        try:
            response = self.ecr_client.describe_images(
                repositoryName=self.repository_name,
                imageIds=[{"imageTag": tag}],
            )

            details = response.get("imageDetails", [])
            if not details:
                return None

            image = details[0]
            digest = image["imageDigest"]

            # Return just the digest
            return digest

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("ImageNotFoundException", "RepositoryNotFoundException"):
                logger.debug(
                    "Image not found in ECR",
                    repository=self.repository_name,
                    tag=tag,
                )
                return None

            logger.error(
                "Failed to get image digest from ECR",
                repository=self.repository_name,
                tag=tag,
                error_code=error_code,
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected error getting image digest from ECR",
                repository=self.repository_name,
                tag=tag,
                error=str(e),
            )
            raise


class DockerRegistryClient:
    """Docker Registry v2 client.

    Handles authentication and proxying for standard Docker Registry v2 API.
    """

    def __init__(
        self,
        config: RegistryConfig,
        username: str = "",
        password: str = "",
        repository_name: str = "",
    ):
        """Initialize Docker Registry client.

        Args:
            config: Registry configuration (URLs)
            username: Registry username (optional, for authenticated registries)
            password: Registry password (optional, for authenticated registries)
            repository_name: Repository name (e.g., "trigger-lambda")
        """
        self.config = config
        self.username = username
        self.password = password
        self.repository_name = repository_name

    def _get_registry_host(self) -> str:
        """Extract registry hostname from registry_url.

        Returns:
            Registry hostname (e.g., "registry:5000")
        """
        # Remove protocol if present
        registry_url = self.config.registry_url
        if "://" in registry_url:
            registry_url = registry_url.split("://", 1)[1]

        # Remove path if present
        if "/" in registry_url:
            return registry_url.split("/")[0]

        return registry_url

    def _get_auth_header(self) -> Optional[str]:
        """Get authorization header for registry authentication.

        Returns:
            Authorization header value or None if no auth configured
        """
        if not self.username or not self.password:
            return None

        # For Docker Registry v2, we use basic auth
        import base64

        credentials = f"{self.username}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    async def proxy(
        self,
        request: Request,
        repository: str,
        path_suffix: str,
    ) -> StreamingResponse:
        """Proxy a Docker Registry request to Docker Registry v2.

        Args:
            request: Original FastAPI request from Docker client
            repository: Docker repository name
            path_suffix: Remaining path after repository (e.g., "/manifests/latest")

        Returns:
            StreamingResponse with registry's response

        Raises:
            Exception if proxying fails
        """
        # Build registry target URL
        registry_host = self._get_registry_host()

        # Determine protocol (http or https)
        if self.config.registry_url.startswith("https://"):
            protocol = "https"
        elif self.config.registry_url.startswith("http://"):
            protocol = "http"
        else:
            # Default to http for internal registries
            protocol = "http"

        target_url = f"{protocol}://{registry_host}/v2/{repository}{path_suffix}"

        logger.info(
            "Proxying request to Docker Registry",
            method=request.method,
            target_url=target_url,
            repository=repository,
        )

        # Use the generic proxy with Docker Registry auth
        return await proxy_request(
            request=request,
            target_url=target_url,
            auth_header=self._get_auth_header(),
            registry_host=registry_host,
            proxy_url=self.config.public_api_url,
        )

    async def get_image_digest(self, tag: str) -> str | None:
        """Get image digest for a given tag from Docker Registry.

        Args:
            tag: Image tag/hash to look up

        Returns:
            Image digest (e.g., "sha256:abc...") or None if not found

        Raises:
            Exception if the operation fails unexpectedly
        """
        import httpx

        # Build registry target URL
        registry_host = self._get_registry_host()

        # Determine protocol (http or https)
        if self.config.registry_url.startswith("https://"):
            protocol = "https"
        elif self.config.registry_url.startswith("http://"):
            protocol = "http"
        else:
            # Default to http for internal registries
            protocol = "http"

        manifest_url = (
            f"{protocol}://{registry_host}/v2/{self.repository_name}/manifests/{tag}"
        )

        logger.info(
            "Fetching image manifest from Docker Registry",
            repository=self.repository_name,
            tag=tag,
            url=manifest_url,
        )

        # Prepare headers
        headers = {
            "Accept": "application/vnd.docker.distribution.manifest.v2+json",
        }

        # Add authentication if configured
        auth_header = self._get_auth_header()
        if auth_header:
            headers["Authorization"] = auth_header

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.head(
                    manifest_url,
                    headers=headers,
                )

                if response.status_code == 404:
                    logger.debug(
                        "Image not found in Docker Registry",
                        repository=self.repository_name,
                        tag=tag,
                    )
                    return None

                if response.status_code != 200:
                    logger.error(
                        "Unexpected status code from Docker Registry",
                        repository=self.repository_name,
                        tag=tag,
                        status_code=response.status_code,
                    )
                    return None

                # Extract digest from Docker-Content-Digest header
                digest = response.headers.get("Docker-Content-Digest")
                if not digest:
                    logger.error(
                        "Docker-Content-Digest header not found in response",
                        repository=self.repository_name,
                        tag=tag,
                    )
                    return None

                # Return just the digest
                return digest

        except httpx.HTTPError as e:
            logger.error(
                "HTTP error while fetching image digest from Docker Registry",
                repository=self.repository_name,
                tag=tag,
                error=str(e),
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected error getting image digest from Docker Registry",
                repository=self.repository_name,
                tag=tag,
                error=str(e),
            )
            raise
