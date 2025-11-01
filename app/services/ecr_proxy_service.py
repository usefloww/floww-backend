"""ECR Proxy Service.

This service handles proxying Docker Registry v2 API requests to AWS ECR.
It manages ECR authentication tokens and streams large image data efficiently.
"""

from typing import AsyncIterator, Optional

import httpx
import structlog
from botocore.exceptions import ClientError
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.settings import settings
from app.utils.aws_ecr import ecr_client

logger = structlog.stdlib.get_logger(__name__)

# Cache for ECR authorization token
# Format: {"token": "...", "expires_at": timestamp}
_ecr_token_cache: Optional[dict] = None


def get_ecr_authorization_token() -> str:
    """Get ECR authorization token for proxying requests.

    Returns the base64-encoded username:password for ECR authentication.
    Tokens are cached and refreshed automatically.

    Returns:
        Base64-encoded ECR authorization token

    Raises:
        Exception if unable to get ECR token
    """
    global _ecr_token_cache

    # TODO: Implement caching with expiry check
    # For now, always fetch fresh token (ECR tokens are valid for 12 hours)

    try:
        response = ecr_client.get_authorization_token()

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


async def stream_request_body(request: Request) -> AsyncIterator[bytes]:
    """Stream request body from client in chunks.

    Args:
        request: FastAPI request object

    Yields:
        Chunks of request body data
    """
    async for chunk in request.stream():
        yield chunk


async def proxy_to_ecr(
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
        ecr_token = get_ecr_authorization_token()
    except Exception as e:
        logger.error("Failed to get ECR token for proxy request", error=str(e))
        raise

    # Build ECR target URL
    # Extract registry hostname from ECR_REGISTRY_URL
    # Format: "501046919403.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda"
    registry_with_repo = settings.ECR_REGISTRY_URL
    if "/" in registry_with_repo:
        registry_host = registry_with_repo.split("/")[0]
    else:
        registry_host = registry_with_repo

    # Use the repository from the request, not the default
    target_url = f"https://{registry_host}/v2/{repository}{path_suffix}"

    logger.info(
        "Proxying request to ECR",
        method=request.method,
        target_url=target_url,
        repository=repository,
    )

    # Prepare headers for ECR request
    headers = {
        "Authorization": f"Basic {ecr_token}",
        "User-Agent": "floww-ecr-proxy",
    }

    # Copy relevant headers from original request
    for header_name in [
        "Content-Type",
        "Content-Length",
        "Docker-Content-Digest",
        "Accept",
    ]:
        if header_name.lower() in request.headers:
            headers[header_name] = request.headers[header_name.lower()]

    # Copy query parameters
    query_params = dict(request.query_params)

    try:
        # Create async HTTP client with appropriate timeouts
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=1800.0,  # 30 minutes for large image downloads
                write=1800.0,  # 30 minutes for large image uploads
                pool=10.0,
            ),
            follow_redirects=True,
        ) as client:
            # Make request to ECR with streaming
            if request.method in ["POST", "PUT", "PATCH"]:
                # Stream request body for uploads
                ecr_response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=query_params,
                    content=stream_request_body(request),
                )
            else:
                # GET, HEAD, DELETE, etc.
                ecr_response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=query_params,
                )

            # Prepare response headers
            response_headers = {}
            for header_name, header_value in ecr_response.headers.items():
                # Skip headers that should not be forwarded
                if header_name.lower() not in [
                    "connection",
                    "keep-alive",
                    "transfer-encoding",
                    "content-encoding",
                ]:
                    response_headers[header_name] = header_value

            # Rewrite Location headers to point back to our proxy instead of ECR
            # This ensures Docker continues using the proxy for all requests
            ecr_registry_host = registry_host  # ECR hostname (e.g., 501046919403.dkr.ecr.us-east-1.amazonaws.com)
            proxy_url = (
                settings.PUBLIC_API_URL
            )  # Our backend URL (e.g., https://api.usefloww.dev)

            # Headers that may contain ECR URLs that need rewriting
            url_headers = ["location", "docker-upload-location"]

            for header_name in url_headers:
                if header_name in response_headers:
                    original_value = response_headers[header_name]
                    # Replace ECR registry hostname with our proxy URL
                    # FROM: https://501046919403.dkr.ecr.us-east-1.amazonaws.com/v2/...
                    # TO: https://api.usefloww.dev/v2/...
                    rewritten_value = original_value.replace(
                        f"https://{ecr_registry_host}",
                        proxy_url,
                    ).replace(
                        f"http://{ecr_registry_host}",
                        proxy_url,
                    )

                    if rewritten_value != original_value:
                        response_headers[header_name] = rewritten_value
                        logger.debug(
                            "Rewrote Location header",
                            header_name=header_name,
                            original=original_value,
                            rewritten=rewritten_value,
                        )

            # Always add Docker Registry API version header
            response_headers["Docker-Distribution-API-Version"] = "registry/2.0"

            logger.info(
                "ECR response received",
                status_code=ecr_response.status_code,
                repository=repository,
            )

            # Stream response back to client
            async def generate():
                async for chunk in ecr_response.aiter_bytes(chunk_size=65536):
                    yield chunk

            return StreamingResponse(
                content=generate(),
                status_code=ecr_response.status_code,
                headers=response_headers,
                media_type=ecr_response.headers.get("content-type"),
            )

    except httpx.TimeoutException as e:
        logger.error(
            "Timeout while proxying to ECR",
            error=str(e),
            target_url=target_url,
        )
        raise
    except httpx.HTTPError as e:
        logger.error(
            "HTTP error while proxying to ECR",
            error=str(e),
            target_url=target_url,
        )
        raise
    except Exception as e:
        logger.error(
            "Unexpected error while proxying to ECR",
            error=str(e),
            target_url=target_url,
        )
        raise
