"""Generic HTTP proxy utilities for Docker Registry API.

This module provides pure utility functions for proxying HTTP requests.
No dependencies on app.* modules to maintain independence and reusability.
"""

from typing import AsyncIterator, Optional

import httpx
import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse

logger = structlog.stdlib.get_logger(__name__)


async def stream_request_body(request: Request) -> AsyncIterator[bytes]:
    """Stream request body from client in chunks.

    Args:
        request: FastAPI request object

    Yields:
        Chunks of request body data
    """
    async for chunk in request.stream():
        yield chunk


async def proxy_request(
    request: Request,
    target_url: str,
    auth_header: Optional[str] = None,
    registry_host: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> StreamingResponse:
    """Proxy an HTTP request to a target URL with streaming support.

    This is a generic proxy function that handles:
    - Streaming request bodies (for uploads)
    - Streaming response bodies (for downloads)
    - Header forwarding and filtering
    - Location header rewriting (for registry redirects)

    Args:
        request: Original FastAPI request from client
        target_url: Full target URL to proxy to
        auth_header: Optional Authorization header value (e.g., "Basic xyz123")
        registry_host: Optional registry hostname for URL rewriting
                      (e.g., "registry.example.com")
        proxy_url: Optional proxy URL for rewriting Location headers
                  (e.g., "https://api.usefloww.dev")

    Returns:
        StreamingResponse with the proxied response

    Raises:
        httpx.HTTPError: If the proxied request fails
    """
    logger.info(
        "Proxying request",
        method=request.method,
        target_url=target_url,
    )

    # Prepare headers for proxied request
    headers = {
        "User-Agent": "floww-registry-proxy",
    }

    # Add authentication if provided
    if auth_header:
        headers["Authorization"] = auth_header

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
            # Make request with streaming
            if request.method in ["POST", "PUT", "PATCH"]:
                # Stream request body for uploads
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=query_params,
                    content=stream_request_body(request),
                )
            else:
                # GET, HEAD, DELETE, etc.
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    params=query_params,
                )

            # Prepare response headers
            response_headers = {}
            for header_name, header_value in response.headers.items():
                # Skip headers that should not be forwarded
                if header_name.lower() not in [
                    "connection",
                    "keep-alive",
                    "transfer-encoding",
                    "content-encoding",
                ]:
                    response_headers[header_name] = header_value

            # Rewrite Location headers if registry_host and proxy_url are provided
            # This ensures Docker continues using our proxy for all requests
            if registry_host and proxy_url:
                url_headers = ["location", "docker-upload-location"]

                for header_name in url_headers:
                    if header_name in response_headers:
                        original_value = response_headers[header_name]
                        # Replace registry hostname with our proxy URL
                        # FROM: https://registry.example.com/v2/...
                        # TO: https://api.usefloww.dev/v2/...
                        rewritten_value = original_value.replace(
                            f"https://{registry_host}",
                            proxy_url,
                        ).replace(
                            f"http://{registry_host}",
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
                "Proxy response received",
                status_code=response.status_code,
                target_url=target_url,
            )

            # Stream response back to client
            async def generate():
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    yield chunk

            return StreamingResponse(
                content=generate(),
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type"),
            )

    except httpx.TimeoutException as e:
        logger.error(
            "Timeout while proxying request",
            error=str(e),
            target_url=target_url,
        )
        raise
    except httpx.HTTPError as e:
        logger.error(
            "HTTP error while proxying request",
            error=str(e),
            target_url=target_url,
        )
        raise
    except Exception as e:
        logger.error(
            "Unexpected error while proxying request",
            error=str(e),
            target_url=target_url,
        )
        raise
