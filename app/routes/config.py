from fastapi import APIRouter
from pydantic import BaseModel

from app.factories import auth_provider_factory
from app.settings import settings

router = APIRouter(tags=["Config"])


class AuthConfig(BaseModel):
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    authorization_endpoint: str
    issuer: str
    jwks_uri: str


class DockerConfig(BaseModel):
    registry_url: str


class ConfigRead(BaseModel):
    auth: AuthConfig
    docker: DockerConfig
    websocket_url: str
    """url to centrifugo"""
    is_cloud: bool


@router.get("/config")
async def get_config():
    auth_provider = auth_provider_factory()
    auth_config = await auth_provider.get_config()

    websocket_url = settings.CENTRIFUGO_PUBLIC_URL.replace("http://", "ws://").replace(
        "https://", "wss://"
    )

    return ConfigRead(
        auth=AuthConfig(
            client_id=auth_config.client_id,
            device_authorization_endpoint=auth_config.device_authorization_endpoint,
            token_endpoint=auth_config.token_endpoint,
            authorization_endpoint=auth_config.authorization_endpoint,
            issuer=auth_config.issuer,
            jwks_uri=auth_config.jwks_uri,
        ),
        docker=DockerConfig(
            registry_url=settings.DOCKER_REGISTRY_URL,
        ),
        websocket_url=websocket_url,
        is_cloud=settings.IS_CLOUD,
    )
