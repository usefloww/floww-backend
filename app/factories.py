from functools import lru_cache

import boto3

from app.packages.auth.providers import (
    AuthProvider,
    OIDCProvider,
    PasswordAuthProvider,
    WorkOSProvider,
)
from app.packages.registry_proxy import (
    DockerRegistryClient,
    ECRRegistryClient,
    RegistryClient,
    RegistryConfig,
)
from app.packages.runtimes.implementations.docker_runtime import DockerRuntime
from app.packages.runtimes.implementations.kubernetes_runtime import KubernetesRuntime
from app.packages.runtimes.implementations.lambda_runtime import LambdaRuntime
from app.packages.runtimes.runtime_types import RuntimeI
from app.settings import settings


@lru_cache
def aws_session_factory() -> boto3.Session:
    return boto3.Session(
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


@lru_cache
def runtime_factory() -> RuntimeI:
    if settings.RUNTIME_TYPE == "lambda":
        lambda_client = aws_session_factory().client("lambda")
        return LambdaRuntime(
            lambda_client=lambda_client,
            execution_role_arn=settings.LAMBDA_EXECUTION_ROLE_ARN,
        )
    elif settings.RUNTIME_TYPE == "docker":
        return DockerRuntime()
    elif settings.RUNTIME_TYPE == "kubernetes":
        return KubernetesRuntime()
    else:
        raise ValueError(f"Invalid runtime type: {settings.RUNTIME_TYPE}")


@lru_cache
def auth_provider_factory() -> AuthProvider:
    if settings.AUTH_TYPE == "none":
        raise RuntimeError(
            "Cannot get auth provider when AUTH_TYPE='none'. "
            "Authentication endpoints should not be called in anonymous mode."
        )

    if settings.AUTH_TYPE == "password":
        return PasswordAuthProvider()

    if settings.AUTH_TYPE == "oidc":
        return OIDCProvider(
            client_id=settings.AUTH_CLIENT_ID,
            client_secret=settings.AUTH_CLIENT_SECRET,
            issuer_url=settings.AUTH_ISSUER_URL,
        )

    # Default to WorkOS
    return WorkOSProvider(
        client_id=settings.AUTH_CLIENT_ID,
        client_secret=settings.AUTH_CLIENT_SECRET,
        issuer_url=settings.AUTH_ISSUER_URL,
    )


@lru_cache
def registry_client_factory() -> RegistryClient:
    """Factory function for creating registry clients based on runtime type.

    Returns:
        RegistryClient instance configured for the current runtime

    Raises:
        ValueError: If runtime type is invalid
    """
    if settings.RUNTIME_TYPE == "lambda":
        ecr_client = aws_session_factory().client("ecr")
        config = RegistryConfig(
            registry_url=settings.REGISTRY_URL,
            public_api_url=settings.PUBLIC_API_URL,
        )
        return ECRRegistryClient(
            config=config,
            ecr_client=ecr_client,
            repository_name=settings.REGISTRY_REPOSITORY_NAME,
        )

    elif settings.RUNTIME_TYPE == "docker":
        config = RegistryConfig(
            registry_url=settings.REGISTRY_URL,
            public_api_url=settings.PUBLIC_API_URL,
        )
        return DockerRegistryClient(
            config=config,
            username=settings.REGISTRY_AUTH_USER,
            password=settings.REGISTRY_AUTH_PASSWORD,
            repository_name=settings.REGISTRY_REPOSITORY_NAME,
        )

    else:
        raise ValueError(f"Invalid runtime type: {settings.RUNTIME_TYPE}")


@lru_cache
def scheduler_factory():
    """Factory function for creating APScheduler instance.

    Returns:
        AsyncIOScheduler: Configured scheduler with PostgreSQL job store

    Note:
        The scheduler is cached as a singleton. Only one instance will be created
        per worker process.
    """
    from app.services.scheduler_service import get_scheduler

    return get_scheduler()
