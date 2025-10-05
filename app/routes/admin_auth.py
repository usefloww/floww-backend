import secrets
import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.settings import settings

router = APIRouter(tags=["Admin Auth"])

# Serializer for signing session data
session_serializer = URLSafeTimedSerializer(settings.SESSION_SECRET_KEY)


def create_session_cookie(jwt_token: str) -> str:
    """Create a signed session cookie value."""
    return session_serializer.dumps(jwt_token)


def get_jwt_from_session_cookie(cookie_value: str) -> Optional[str]:
    """Extract JWT token from signed session cookie."""
    try:
        # 30 day expiration for session cookies
        return session_serializer.loads(cookie_value, max_age=30 * 24 * 3600)
    except BadSignature:
        return None


def is_safe_redirect_url(url: str, request: Request) -> bool:
    """Check if the redirect URL is safe to prevent open redirects."""
    if not url:
        return False

    # Allow relative URLs starting with /admin
    if url.startswith("/admin"):
        return True

    # Allow same-origin URLs
    parsed_url = urllib.parse.urlparse(url)
    request_host = request.headers.get("host", "")

    # If no host in URL, it's relative and safe
    if not parsed_url.netloc:
        return url.startswith("/admin")

    # Check if it's the same host
    return parsed_url.netloc.lower() == request_host.lower()


@router.get("/auth/login")
async def admin_login(
    request: Request, next_url: Optional[str] = Query(None, alias="next")
):
    """Initiate WorkOS OAuth login flow for admin access."""

    # Validate next URL to prevent open redirects
    if next_url and not is_safe_redirect_url(next_url, request):
        next_url = "/admin"
    elif not next_url:
        next_url = "/admin"

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)

    # Store next URL in the state (we'll encode it)
    state_data = {"csrf": state, "next": next_url}
    signed_state = session_serializer.dumps(state_data)

    # Build WorkOS authorization URL
    auth_params = {
        "client_id": settings.WORKOS_CLIENT_ID,
        "redirect_uri": settings.WORKOS_REDIRECT_URI,
        "response_type": "code",
        "state": signed_state,
        "scope": "profile email",
        "provider": "authkit",  # Use AuthKit provider for WorkOS
    }

    auth_url = (
        f"{settings.WORKOS_API_URL}/user_management/authorize?"
        + urllib.parse.urlencode(auth_params)
    )

    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/callback")
async def admin_auth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle WorkOS OAuth callback and set session cookie."""

    # Check for OAuth errors
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth error: {error}"
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code or state",
        )

    # Verify and decode state
    try:
        state_data = session_serializer.loads(
            state, max_age=600
        )  # 10 minute expiration
        next_url = state_data.get("next", "/admin")
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    # Exchange authorization code for JWT token
    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                f"{settings.WORKOS_API_URL}/user_management/authenticate",
                json={
                    "client_id": settings.WORKOS_CLIENT_ID,
                    "client_secret": settings.WORKOS_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                },
                headers={"Content-Type": "application/json"},
            )

            if token_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to exchange code for token: {token_response.text}",
                )

            token_data = token_response.json()
            access_token = token_data.get("access_token")

            if not access_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No access token received from WorkOS",
                )

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to communicate with WorkOS: {str(e)}",
        )

    # Create secure session cookie
    session_cookie_value = create_session_cookie(access_token)

    # Create redirect response
    response = RedirectResponse(url=next_url, status_code=status.HTTP_302_FOUND)

    # Set secure session cookie
    response.set_cookie(
        key="session",
        value=session_cookie_value,
        max_age=30 * 24 * 3600,  # 30 days
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
    )

    return response


@router.post("/auth/logout")
async def admin_logout():
    """Logout from admin interface."""
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("session")
    return response
