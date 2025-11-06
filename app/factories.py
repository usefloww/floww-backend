from functools import lru_cache

import boto3

from app.packages.auth.providers import AuthProvider, OIDCProvider, WorkOSProvider
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
