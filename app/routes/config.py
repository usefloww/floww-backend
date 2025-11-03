from fastapi import APIRouter

from app.auth.oidc import get_oidc_discovery
from app.settings import settings

router = APIRouter(tags=["Config"])


@router.get("/config")
async def get_config():
    discovery = await get_oidc_discovery(settings.AUTH_ISSUER_URL)

    auth_config = {
        "client_id": settings.AUTH_DEVICE_CLIENT_ID,
        "device_authorization_endpoint": discovery.get("device_authorization_endpoint"),
        "token_endpoint": discovery.get("token_endpoint"),
        "authorization_endpoint": discovery.get("authorization_endpoint"),
        "issuer": discovery.get("issuer"),
        "jwks_uri": discovery.get("jwks_uri"),
    }

    websocket_url = settings.PUBLIC_API_URL.replace("http://", "ws://").replace(
        "https://", "wss://"
    )

    return {
        "auth": auth_config,
        "websocket_url": f"{websocket_url}/connection/websocket",
    }
