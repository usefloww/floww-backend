"""
Device Authorization Grant routes (RFC 8628).

Implements OAuth2 device flow for CLI and other device authentication.
Only available when AUTH_TYPE='password'.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.deps.db import SessionDep
from app.factories import auth_provider_factory
from app.models import DeviceCodeStatus
from app.packages.auth.providers import PasswordAuthProvider
from app.services.device_code_service import (
    approve_device_code,
    check_device_code_status,
    create_device_authorization,
    delete_device_code,
    get_device_code_by_user_code,
)
from app.services.refresh_token_service import (
    create_refresh_token,
    revoke_refresh_token,
    validate_and_update_refresh_token,
)
from app.settings import settings
from app.utils.session import get_jwt_from_session_cookie

router = APIRouter(tags=["Device Auth"])


class DeviceAuthorizationResponse(BaseModel):
    """Response for device authorization initiation."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class TokenResponse(BaseModel):
    """Response for successful token exchange."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 2592000  # 30 days in seconds


class TokenErrorResponse(BaseModel):
    """Error response for token polling."""

    error: str
    error_description: Optional[str] = None


@router.post("/auth/device/authorize", response_model=DeviceAuthorizationResponse)
async def device_authorize(
    session: SessionDep,
    request: Request,
):
    """
    Initiate device authorization flow.

    Returns device_code (for polling) and user_code (for user entry).
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device authorization is only available with password authentication",
        )

    # Create device authorization
    auth_data = await create_device_authorization(session)

    # Build verification URI
    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    verification_uri = f"{scheme}://{host}/auth/device/verify"
    verification_uri_complete = f"{verification_uri}?user_code={auth_data['user_code']}"

    return DeviceAuthorizationResponse(
        device_code=auth_data["device_code"],
        user_code=auth_data["user_code"],
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=auth_data["expires_in"],
        interval=auth_data["interval"],
    )


@router.get("/auth/device/verify", response_class=HTMLResponse)
async def device_verify_page(
    session: SessionDep,
    request: Request,
    user_code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """
    Display device verification page.

    If user is already logged in via session cookie, show simple approval page.
    Otherwise, redirect to login with return URL.
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device authorization is only available with password authentication",
        )

    # Check if user is already authenticated
    session_cookie = request.cookies.get("session")
    authenticated_user_id = None

    if session_cookie:
        jwt_token = get_jwt_from_session_cookie(session_cookie)
        if jwt_token:
            try:
                auth_provider = auth_provider_factory()
                if isinstance(auth_provider, PasswordAuthProvider):
                    token_user = await auth_provider.validate_token(jwt_token)
                    authenticated_user_id = token_user.sub
            except Exception:
                # Invalid token, user not authenticated
                pass

    # Pre-fill user code if provided in URL
    user_code_value = user_code.upper() if user_code else ""
    error_message = error if error else ""

    if authenticated_user_id:
        # User is logged in - show simple approval page
        return HTMLResponse(
            content=_get_device_approve_html(user_code_value, error_message)
        )
    else:
        # User not logged in - redirect to login with return URL
        return_url = (
            f"/auth/device/verify?user_code={user_code_value}"
            if user_code_value
            else "/auth/device/verify"
        )
        return RedirectResponse(
            url=f"/auth/login?next={return_url}", status_code=status.HTTP_302_FOUND
        )


@router.post("/auth/device/verify", response_class=HTMLResponse)
async def device_verify_submit(
    session: SessionDep,
    request: Request,
    user_code: str = Form(...),
):
    """
    Process device verification submission.

    For authenticated users (via session cookie), approves the device code.
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device authorization is only available with password authentication",
        )

    # Check if user is authenticated
    session_cookie = request.cookies.get("session")
    authenticated_user_id = None

    if session_cookie:
        jwt_token = get_jwt_from_session_cookie(session_cookie)
        if jwt_token:
            try:
                auth_provider = auth_provider_factory()
                if isinstance(auth_provider, PasswordAuthProvider):
                    token_user = await auth_provider.validate_token(jwt_token)
                    authenticated_user_id = token_user.sub
            except Exception:
                pass

    if not authenticated_user_id:
        # User not authenticated, redirect to login
        return RedirectResponse(
            url=f"/auth/login?next=/auth/device/verify?user_code={user_code}",
            status_code=status.HTTP_302_FOUND,
        )

    # Normalize user code to uppercase
    user_code = user_code.upper().strip()

    # Check if device code exists
    device_code_record = await get_device_code_by_user_code(session, user_code)
    if device_code_record is None:
        return HTMLResponse(
            content=_get_device_approve_html(
                user_code, "Invalid or expired device code. Please try again."
            )
        )

    # Approve the device code
    success = await approve_device_code(session, user_code, UUID(authenticated_user_id))
    if not success:
        return HTMLResponse(
            content=_get_device_approve_html(
                user_code,
                "Failed to authorize device. The code may have expired or already been used.",
            )
        )

    # Success! Show confirmation page
    return HTMLResponse(content=_get_device_success_html())


@router.post("/auth/device/token")
async def device_token(
    session: SessionDep,
    device_code: str = Form(...),
    grant_type: str = Form(...),
):
    """
    Token endpoint for device flow polling.

    Returns access token if authorized, or appropriate error for pending/denied/expired.
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device authorization is only available with password authentication",
        )

    # Validate grant_type (RFC 8628 requires this)
    if grant_type != "urn:ietf:params:oauth:grant-type:device_code":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=TokenErrorResponse(
                error="unsupported_grant_type",
                error_description="grant_type must be urn:ietf:params:oauth:grant-type:device_code",
            ).model_dump(),
        )

    # Check device code status
    code_status, user_id = await check_device_code_status(session, device_code)

    if code_status == DeviceCodeStatus.PENDING:
        # Authorization still pending
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=TokenErrorResponse(
                error="authorization_pending",
                error_description="User has not yet authorized the device",
            ).model_dump(),
        )

    elif code_status == DeviceCodeStatus.DENIED:
        # User denied authorization
        await delete_device_code(session, device_code)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=TokenErrorResponse(
                error="access_denied",
                error_description="User denied the authorization request",
            ).model_dump(),
        )

    elif code_status == DeviceCodeStatus.EXPIRED:
        # Code expired
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=TokenErrorResponse(
                error="expired_token",
                error_description="The device code has expired",
            ).model_dump(),
        )

    elif code_status == DeviceCodeStatus.APPROVED:
        if user_id is None:
            # Should not happen, but handle it
            raise RuntimeError("Device code approved but user_id is missing")

        # Generate JWT token
        auth_provider = auth_provider_factory()
        if not isinstance(auth_provider, PasswordAuthProvider):
            raise RuntimeError("Password auth provider not configured correctly")

        jwt_token = auth_provider.create_token(str(user_id))

        # Create refresh token
        refresh_token = await create_refresh_token(
            session=session,
            user_id=user_id,
            device_name="CLI Device",  # Could be customized based on user agent
        )

        # Delete the device code (single use)
        await delete_device_code(session, device_code)

        # Return access token and refresh token
        return JSONResponse(
            content=TokenResponse(
                access_token=jwt_token,
                refresh_token=refresh_token,
                token_type="Bearer",
                expires_in=2592000,  # 30 days
            ).model_dump()
        )

    else:
        # Unknown status
        raise ValueError(f"Unknown device code status: {code_status}")


@router.post("/auth/token/refresh")
async def token_refresh(
    session: SessionDep,
    refresh_token: str = Form(...),
):
    """
    Refresh endpoint - exchange refresh token for new access token.

    The refresh token remains valid and can be reused multiple times.
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token refresh is only available with password authentication",
        )

    # Validate refresh token
    user_id = await validate_and_update_refresh_token(session, refresh_token)

    if user_id is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=TokenErrorResponse(
                error="invalid_grant",
                error_description="Invalid or revoked refresh token",
            ).model_dump(),
        )

    # Generate new access token
    auth_provider = auth_provider_factory()
    if not isinstance(auth_provider, PasswordAuthProvider):
        raise RuntimeError("Password auth provider not configured correctly")

    jwt_token = auth_provider.create_token(str(user_id))

    # Return new access token (same refresh token can be reused)
    return JSONResponse(
        content={
            "access_token": jwt_token,
            "token_type": "Bearer",
            "expires_in": 2592000,  # 30 days
        }
    )


@router.post("/auth/token/revoke")
async def token_revoke(
    session: SessionDep,
    refresh_token: str = Form(...),
):
    """
    Revoke a refresh token.

    Use this endpoint for CLI logout functionality.
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token revocation is only available with password authentication",
        )

    # Revoke the refresh token
    success = await revoke_refresh_token(session, refresh_token)

    if success:
        return JSONResponse(
            content={"message": "Refresh token revoked successfully"},
            status_code=status.HTTP_200_OK,
        )
    else:
        return JSONResponse(
            content={"message": "Refresh token not found or already revoked"},
            status_code=status.HTTP_200_OK,  # Still return 200 for idempotency
        )


def _get_device_verify_html(user_code: str = "", error: str = "") -> str:
    """
    Generate the HTML for device verification page.

    Allows user to enter device code and credentials.
    """
    error_html = f'<p style="color: red;">{error}</p>' if error else ""
    user_code_readonly = (
        'readonly style="background-color: #f0f0f0;"' if user_code else ""
    )

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Floww - Device Authorization</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
            margin-bottom: 10px;
        }}
        p {{
            text-align: center;
            color: #666;
            margin-bottom: 20px;
        }}
        input {{
            width: 100%;
            padding: 8px;
            margin: 5px 0 15px 0;
            box-sizing: border-box;
        }}
        button[type="submit"] {{
            width: 100%;
            padding: 10px;
            background: #007bff;
            color: white;
            border: none;
            cursor: pointer;
        }}
        button[type="submit"]:hover {{
            background: #0056b3;
        }}
        .code-display {{
            text-align: center;
            font-size: 24px;
            font-weight: bold;
            letter-spacing: 2px;
            margin: 20px 0;
            padding: 15px;
            background: #f8f9fa;
            border: 2px solid #dee2e6;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <h1>Authorize Device</h1>
    <p>Enter the code shown on your device and your credentials</p>
    {error_html}

    <form action="/auth/device/verify" method="POST">
        <label>Device Code</label>
        <input
            type="text"
            name="user_code"
            value="{user_code}"
            placeholder="XXXX-XXXX"
            pattern="[A-Z0-9]{{4}}-[A-Z0-9]{{4}}"
            maxlength="9"
            required
            autofocus
            {user_code_readonly}
        >

        <label>Username</label>
        <input type="text" name="username" required>

        <label>Password</label>
        <input type="password" name="password" required>

        <button type="submit">Authorize Device</button>
    </form>
</body>
</html>
"""


def _get_device_approve_html(user_code: str = "", error: str = "") -> str:
    """
    Generate the HTML for device approval page (for authenticated users).

    Shows device code and simple authorize button (no username/password).
    """
    error_html = f'<p style="color: red;">{error}</p>' if error else ""
    user_code_readonly = (
        'readonly style="background-color: #f0f0f0;"' if user_code else ""
    )

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Floww - Authorize Device</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
            margin-bottom: 10px;
        }}
        p {{
            text-align: center;
            color: #666;
            margin-bottom: 20px;
        }}
        input {{
            width: 100%;
            padding: 8px;
            margin: 5px 0 15px 0;
            box-sizing: border-box;
        }}
        button[type="submit"] {{
            width: 100%;
            padding: 10px;
            background: #007bff;
            color: white;
            border: none;
            cursor: pointer;
            font-size: 16px;
        }}
        button[type="submit"]:hover {{
            background: #0056b3;
        }}
        .code-display {{
            text-align: center;
            font-size: 32px;
            font-weight: bold;
            letter-spacing: 3px;
            margin: 30px 0;
            padding: 20px;
            background: #f8f9fa;
            border: 2px solid #007bff;
            border-radius: 8px;
            color: #007bff;
        }}
        .info {{
            background: #e7f3ff;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <h1>Authorize Device</h1>
    <p>Confirm the code shown on your device</p>
    {error_html}

    <form action="/auth/device/verify" method="POST">
        <div class="info">
            ℹ️ You are authorizing a CLI device to access your Floww account.
        </div>

        <label>Device Code</label>
        <input
            type="text"
            name="user_code"
            value="{user_code}"
            placeholder="XXXX-XXXX"
            pattern="[A-Z0-9]{{4}}-[A-Z0-9]{{4}}"
            maxlength="9"
            required
            autofocus
            {user_code_readonly}
        >

        <button type="submit">Authorize This Device</button>
    </form>
</body>
</html>
"""


def _get_device_success_html() -> str:
    """
    Generate the HTML for successful device authorization.
    """
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Floww - Device Authorized</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
            text-align: center;
        }
        h1 {
            color: #28a745;
            margin-bottom: 20px;
        }
        p {
            color: #666;
            font-size: 16px;
        }
        .success-icon {
            font-size: 64px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <div class="success-icon">✓</div>
    <h1>Device Authorized!</h1>
    <p>You have successfully authorized the device.</p>
    <p>You can now close this window and return to your device.</p>
</body>
</html>
"""
