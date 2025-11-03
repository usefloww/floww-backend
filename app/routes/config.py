from fastapi import APIRouter

from app.factories import get_auth_provider
from app.settings import settings

router = APIRouter(tags=["Config"])


@router.get("/config")
async def get_config():
    auth_provider = get_auth_provider()
    auth_config = await auth_provider.get_config()

    auth_config = {
        "client_id": auth_config.client_id,
        "device_authorization_endpoint": auth_config.device_authorization_endpoint,
        "token_endpoint": auth_config.token_endpoint,
        "authorization_endpoint": auth_config.authorization_endpoint,
        "issuer": auth_config.issuer,
        "jwks_uri": auth_config.jwks_uri,
    }

    websocket_url = settings.PUBLIC_API_URL.replace("http://", "ws://").replace(
        "https://", "wss://"
    )

    return {
        "auth": auth_config,
        "websocket_url": f"{websocket_url}/connection/websocket",
    }
