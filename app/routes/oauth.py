"""
OAuth routes for third-party provider authentication.

Handles the OAuth authorization flow:
1. GET /oauth/{provider}/authorize - Generate authorization URL
2. GET /oauth/{provider}/callback - Handle OAuth callback, store tokens
"""

import json
import secrets
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from itsdangerous import BadSignature
from pydantic import BaseModel
from sqlalchemy import select

from app.deps.auth import CurrentUser
from app.deps.db import TransactionSessionDep
from app.models import Namespace, OrganizationMember, Provider
from app.services.oauth_service import get_oauth_provider
from app.utils.encryption import decrypt_secret, encrypt_secret
from app.utils.session import session_serializer

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/oauth", tags=["OAuth"])


class OAuthAuthorizeResponse(BaseModel):
    auth_url: str


@router.get("/{provider_name}/authorize", response_model=OAuthAuthorizeResponse)
async def oauth_authorize(
    provider_name: str,
    current_user: CurrentUser,
    request: Request,
    provider_id: UUID = Query(..., description="Provider ID to store tokens in"),
):
    """
    Generate OAuth authorization URL.

    The frontend should redirect the user (or open a popup) to the returned auth_url.
    After authorization, Google will redirect to /oauth/{provider}/callback.
    """
    try:
        oauth_provider = get_oauth_provider(provider_name)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown OAuth provider: {provider_name}",
        )

    # Build callback URL
    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/oauth/{provider_name}/callback"

    # Get scopes from the provider setup step (for now, use default scopes)
    # In a full implementation, these would come from the provider type definition
    scopes = _get_scopes_for_provider(provider_name)

    # Create state with CSRF token and provider_id
    state_data = {
        "csrf": secrets.token_urlsafe(32),
        "provider_id": str(provider_id),
        "user_id": str(current_user.id),
    }
    signed_state = session_serializer.dumps(state_data)

    auth_url = oauth_provider.get_authorization_url(
        scopes=scopes,
        state=signed_state,
        redirect_uri=redirect_uri,
    )

    logger.info(
        "Generated OAuth authorization URL",
        provider=provider_name,
        provider_id=str(provider_id),
    )

    return OAuthAuthorizeResponse(auth_url=auth_url)


@router.get("/{provider_name}/callback", response_class=HTMLResponse)
async def oauth_callback(
    provider_name: str,
    session: TransactionSessionDep,
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """
    Handle OAuth callback from provider.

    Exchanges authorization code for tokens and stores them in the provider's config.
    Returns HTML that sends a postMessage to the opener and closes the popup.
    """
    if error:
        logger.warning("OAuth callback error", provider=provider_name, error=error)
        return _oauth_result_html(success=False, error=error)

    if not code or not state:
        return _oauth_result_html(
            success=False, error="Missing authorization code or state"
        )

    # Verify state
    try:
        state_data = session_serializer.loads(state, max_age=600)  # 10 minute expiry
        provider_id = UUID(state_data["provider_id"])
        user_id = UUID(state_data["user_id"])
    except (BadSignature, KeyError, ValueError) as e:
        logger.warning("Invalid OAuth state", provider=provider_name, error=str(e))
        return _oauth_result_html(success=False, error="Invalid or expired state")

    # Verify user has access to the provider
    # Select only Provider.id for the IN clause (IN requires a single column)
    accessible_provider_ids = select(Provider.id).where(
        Provider.namespace.has(
            Namespace.organization_owner_id.in_(
                select(OrganizationMember.organization_id).where(
                    OrganizationMember.user_id == user_id
                )
            )
        )
    )
    result = await session.execute(
        select(Provider)
        .where(Provider.id == provider_id)
        .where(Provider.id.in_(accessible_provider_ids))
    )
    provider = result.scalar_one_or_none()

    if not provider:
        logger.warning(
            "Provider not found or access denied",
            provider=provider_name,
            provider_id=str(provider_id),
        )
        return _oauth_result_html(success=False, error="Provider not found")

    # Exchange code for tokens
    try:
        oauth_provider = get_oauth_provider(provider_name)
    except ValueError:
        return _oauth_result_html(
            success=False, error=f"Unknown OAuth provider: {provider_name}"
        )

    # Build callback URL (same as authorize)
    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/oauth/{provider_name}/callback"

    try:
        tokens = await oauth_provider.exchange_code(code, redirect_uri)
    except Exception as e:
        logger.error(
            "Failed to exchange OAuth code", provider=provider_name, error=str(e)
        )
        return _oauth_result_html(
            success=False, error="Failed to exchange authorization code"
        )

    # Update provider config with tokens
    existing_config = json.loads(decrypt_secret(provider.encrypted_config))
    existing_config.update(tokens.to_dict())
    provider.encrypted_config = encrypt_secret(json.dumps(existing_config))

    await session.flush()

    logger.info(
        "OAuth tokens stored successfully",
        provider=provider_name,
        provider_id=str(provider_id),
    )

    return _oauth_result_html(success=True)


def _get_scopes_for_provider(provider_name: str) -> list[str]:
    """Get default OAuth scopes for a provider."""
    # These are the base scopes. The actual scopes may be extended
    # based on the specific provider type (e.g., google_calendar)
    scopes_map = {
        "google": [
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
    }
    return scopes_map.get(provider_name, [])


def _oauth_result_html(success: bool, error: Optional[str] = None) -> str:
    """Generate HTML that communicates result to opener and closes popup."""
    result_data = {"success": success}
    if error:
        result_data["error"] = error

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>OAuth {("Success" if success else "Error")}</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #f5f5f5;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .success {{ color: #10b981; }}
        .error {{ color: #ef4444; }}
    </style>
</head>
<body>
    <div class="container">
        <h2 class="{"success" if success else "error"}">
            {("Connected successfully!" if success else f"Connection failed: {error or 'Unknown error'}")}
        </h2>
        <p>{"This window will close automatically." if success else "Please close this window and try again."}</p>
    </div>
    <script>
        const result = {json.dumps(result_data)};
        if (window.opener) {{
            window.opener.postMessage({{ type: 'oauth_callback', ...result }}, '*');
            if (result.success) {{
                setTimeout(() => window.close(), 1500);
            }}
        }}
    </script>
</body>
</html>
"""
