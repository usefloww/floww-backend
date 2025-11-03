from fastapi import APIRouter

from app.settings import settings

router = APIRouter(tags=["Config"])


@router.get("/config")
async def get_config():
    # Temporary hardcoded values for WorkOS
    auth_config = {
        "client_id": settings.AUTH_CLIENT_ID,
        "device_authorization_endpoint": "https://api.workos.com/user_management/authorize/device",
        "token_endpoint": "https://api.workos.com/user_management/authenticate",
        "authorization_endpoint": "https://api.workos.com/user_management/authorize",
        "issuer": "https://api.workos.com/user_management",
        "jwks_uri": "https://api.workos.com/user_management/jwks",
    }

    # discovery = await get_oidc_discovery(settings.AUTH_ISSUER_URL)
    # auth_config = {
    #     "client_id": settings.AUTH_CLIENT_ID,
    #     "device_authorization_endpoint": discovery.get("device_authorization_endpoint"),
    #     "token_endpoint": discovery.get("token_endpoint"),
    #     "authorization_endpoint": discovery.get("authorization_endpoint"),
    #     "issuer": discovery.get("issuer"),
    #     "jwks_uri": discovery.get("jwks_uri"),
    # }

    websocket_url = settings.PUBLIC_API_URL.replace("http://", "ws://").replace(
        "https://", "wss://"
    )

    return {
        "auth": auth_config,
        "websocket_url": f"{websocket_url}/connection/websocket",
    }
