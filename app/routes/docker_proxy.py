"""Docker Registry v2 API Proxy.

This module implements the Docker Registry HTTP API V2 specification,
proxying authenticated requests to AWS ECR.

See: https://docs.docker.com/registry/spec/api/
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps.docker_auth import DockerUser
from app.services.ecr_proxy_service import proxy_to_ecr

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(tags=["Docker Proxy"])


@router.get("/v2/")
async def registry_version_check():
    """Docker Registry API version check.

    This endpoint is called by Docker CLI to verify the registry supports v2 API.
    Per spec, this endpoint returns 200 with the API version header.

    No authentication is required for this endpoint per Docker spec.
    """
    logger.debug("Docker registry version check")

    return JSONResponse(
        status_code=200,
        content={},
        headers={"Docker-Distribution-API-Version": "registry/2.0"},
    )


@router.get("/v2/{repository:path}/manifests/{reference}")
async def get_manifest(
    request: Request,
    repository: str,
    reference: str,
    current_user: DockerUser,
):
    """Pull image manifest from ECR.

    Args:
        repository: Docker repository name (e.g., "trigger-lambda")
        reference: Tag or digest (e.g., "latest" or "sha256:abc...")
        current_user: Authenticated user from WorkOS token

    Returns:
        Image manifest JSON from ECR
    """
    logger.info(
        "Docker pull manifest",
        repository=repository,
        reference=reference,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/manifests/{reference}",
    )


@router.head("/v2/{repository:path}/manifests/{reference}")
async def check_manifest(
    request: Request,
    repository: str,
    reference: str,
    current_user: DockerUser,
):
    """Check if image manifest exists in ECR.

    Args:
        repository: Docker repository name
        reference: Tag or digest
        current_user: Authenticated user

    Returns:
        Response with headers only (no body)
    """
    logger.info(
        "Docker check manifest",
        repository=repository,
        reference=reference,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/manifests/{reference}",
    )


@router.put("/v2/{repository:path}/manifests/{reference}")
async def push_manifest(
    request: Request,
    repository: str,
    reference: str,
    current_user: DockerUser,
):
    """Push image manifest to ECR.

    Args:
        repository: Docker repository name
        reference: Tag or digest
        current_user: Authenticated user

    Returns:
        Success response from ECR
    """
    logger.info(
        "Docker push manifest",
        repository=repository,
        reference=reference,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/manifests/{reference}",
    )


@router.delete("/v2/{repository:path}/manifests/{reference}")
async def delete_manifest(
    request: Request,
    repository: str,
    reference: str,
    current_user: DockerUser,
):
    """Delete image manifest from ECR.

    Args:
        repository: Docker repository name
        reference: Tag or digest
        current_user: Authenticated user

    Returns:
        Success response from ECR
    """
    logger.info(
        "Docker delete manifest",
        repository=repository,
        reference=reference,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/manifests/{reference}",
    )


@router.get("/v2/{repository:path}/blobs/{digest}")
async def get_blob(
    request: Request,
    repository: str,
    digest: str,
    current_user: DockerUser,
):
    """Download image layer blob from ECR.

    Args:
        repository: Docker repository name
        digest: Blob digest (e.g., "sha256:abc...")
        current_user: Authenticated user

    Returns:
        Streaming blob data from ECR
    """
    logger.info(
        "Docker pull blob",
        repository=repository,
        digest=digest,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/blobs/{digest}",
    )


@router.head("/v2/{repository:path}/blobs/{digest}")
async def check_blob(
    request: Request,
    repository: str,
    digest: str,
    current_user: DockerUser,
):
    """Check if blob exists in ECR.

    Args:
        repository: Docker repository name
        digest: Blob digest
        current_user: Authenticated user

    Returns:
        Response with headers only
    """
    logger.info(
        "Docker check blob",
        repository=repository,
        digest=digest,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/blobs/{digest}",
    )


@router.delete("/v2/{repository:path}/blobs/{digest}")
async def delete_blob(
    request: Request,
    repository: str,
    digest: str,
    current_user: DockerUser,
):
    """Delete blob from ECR.

    Args:
        repository: Docker repository name
        digest: Blob digest
        current_user: Authenticated user

    Returns:
        Success response from ECR
    """
    logger.info(
        "Docker delete blob",
        repository=repository,
        digest=digest,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/blobs/{digest}",
    )


@router.post("/v2/{repository:path}/blobs/uploads/")
async def initiate_blob_upload(
    request: Request,
    repository: str,
    current_user: DockerUser,
):
    """Initiate a blob upload session.

    Docker will use this to start uploading an image layer.

    Args:
        repository: Docker repository name
        current_user: Authenticated user

    Returns:
        Upload session information with Location header
    """
    logger.info(
        "Docker initiate blob upload",
        repository=repository,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix="/blobs/uploads/",
    )


@router.patch("/v2/{repository:path}/blobs/uploads/{upload_uuid}")
async def upload_blob_chunk(
    request: Request,
    repository: str,
    upload_uuid: str,
    current_user: DockerUser,
):
    """Upload a chunk of blob data.

    Docker streams image layers in chunks using PATCH requests.

    Args:
        repository: Docker repository name
        upload_uuid: Upload session UUID
        current_user: Authenticated user

    Returns:
        Upload progress response
    """
    logger.info(
        "Docker upload blob chunk",
        repository=repository,
        upload_uuid=upload_uuid,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/blobs/uploads/{upload_uuid}",
    )


@router.put("/v2/{repository:path}/blobs/uploads/{upload_uuid}")
async def complete_blob_upload(
    request: Request,
    repository: str,
    upload_uuid: str,
    current_user: DockerUser,
):
    """Complete a blob upload session.

    Docker calls this after streaming all chunks to finalize the upload.

    Args:
        repository: Docker repository name
        upload_uuid: Upload session UUID
        current_user: Authenticated user

    Returns:
        Success response with blob location
    """
    logger.info(
        "Docker complete blob upload",
        repository=repository,
        upload_uuid=upload_uuid,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix=f"/blobs/uploads/{upload_uuid}",
    )


@router.get("/v2/{repository:path}/tags/list")
async def list_tags(
    request: Request,
    repository: str,
    current_user: DockerUser,
):
    """List all tags for a repository.

    Args:
        repository: Docker repository name
        current_user: Authenticated user

    Returns:
        JSON list of tags
    """
    logger.info(
        "Docker list tags",
        repository=repository,
        user_id=str(current_user.id),
    )

    return await proxy_to_ecr(
        request=request,
        repository=repository,
        path_suffix="/tags/list",
    )


@router.get("/v2/_catalog")
async def list_repositories(
    request: Request,
    current_user: DockerUser,
):
    """List all repositories in the registry.

    Args:
        current_user: Authenticated user

    Returns:
        JSON list of repositories
    """
    logger.info(
        "Docker list repositories",
        user_id=str(current_user.id),
    )

    # This endpoint doesn't have a repository prefix
    return await proxy_to_ecr(
        request=request,
        repository="",  # No repository for catalog
        path_suffix="/_catalog",
    )
