import secrets
from typing import Optional

import jwt as pyjwt
from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature
from sqlalchemy import func, select

from app.deps.db import SessionDep
from app.factories import auth_provider_factory
from app.models import User
from app.packages.auth.providers import PasswordAuthProvider
from app.services.refresh_token_service import revoke_all_user_tokens
from app.services.user_service import create_password_user, get_user_by_username
from app.settings import settings
from app.utils.password import verify_password
from app.utils.session import (
    create_session_cookie,
    get_jwt_from_session_cookie,
    is_safe_redirect_url,
    session_serializer,
)

router = APIRouter(tags=["Admin Auth"])


@router.get("/auth/login", response_class=HTMLResponse)
async def admin_login(
    session: SessionDep,
    request: Request,
    next_url: Optional[str] = Query(None, alias="next"),
    prompt: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    # If AUTH_TYPE is 'none', redirect to home since no authentication is needed
    if settings.AUTH_TYPE == "none":
        if not next_url or not is_safe_redirect_url(next_url, request):
            next_url = "/"
        return RedirectResponse(url=next_url, status_code=status.HTTP_302_FOUND)

    if not next_url or not is_safe_redirect_url(next_url, request):
        next_url = "/"

    # If AUTH_TYPE is 'password', check if we need setup or login
    if settings.AUTH_TYPE == "password":
        # Check if any users exist

        result = await session.execute(select(func.count(User.id)))
        user_count = result.scalar()

        error_message = error if error else ""
        is_setup = user_count == 0

        return HTMLResponse(
            content=_get_password_login_html(next_url, error_message, is_setup)
        )

    # OAuth flow for OIDC/WorkOS
    state = secrets.token_urlsafe(32)
    state_data = {"csrf": state, "next": next_url}
    signed_state = session_serializer.dumps(state_data)

    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/auth/callback"

    auth_provider = auth_provider_factory()

    auth_url = await auth_provider.get_authorization_url(
        redirect_uri=redirect_uri,
        state=signed_state,
        prompt=prompt,
    )

    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/callback")
async def admin_auth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    # If AUTH_TYPE is 'none', redirect to home since no authentication callback is needed
    if settings.AUTH_TYPE == "none":
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth error: {error}"
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code or state",
        )

    try:
        state_data = session_serializer.loads(state, max_age=600)
        next_url = state_data.get("next", "/")
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/auth/callback"

    auth_provider = auth_provider_factory()
    token_data = await auth_provider.exchange_code_for_token(code, redirect_uri)
    jwt_token = token_data.get("id_token") or token_data.get("access_token")

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No access token or id token received from auth provider",
        )

    session_cookie_value = create_session_cookie(jwt_token)
    response = RedirectResponse(url=next_url, status_code=status.HTTP_302_FOUND)

    response.set_cookie(
        key="session",
        value=session_cookie_value,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=False,
        samesite="lax",
    )

    return response


@router.post("/auth/logout")
async def admin_logout(session: SessionDep, request: Request):
    # If AUTH_TYPE is 'none', just redirect to home since no logout is needed
    if settings.AUTH_TYPE == "none":
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Extract session token to revoke it
    session_cookie = request.cookies.get("session")
    if session_cookie:
        jwt_token = get_jwt_from_session_cookie(session_cookie)
        if jwt_token:
            try:
                auth_provider = auth_provider_factory()

                # For password auth, also revoke all refresh tokens
                if isinstance(auth_provider, PasswordAuthProvider):
                    # Extract user_id from JWT to revoke all refresh tokens

                    try:
                        decoded = pyjwt.decode(
                            jwt_token,
                            options={
                                "verify_signature": False
                            },  # We just need the user_id
                        )
                        user_id = decoded.get("sub")
                        if user_id:
                            from uuid import UUID

                            await revoke_all_user_tokens(session, UUID(user_id))
                    except Exception as e:
                        print(f"Refresh token revocation error: {e}")

                await auth_provider.revoke_session(jwt_token)
            except Exception as e:
                # Log but don't fail logout if revocation fails
                print(f"Session revocation error: {e}")

    response = RedirectResponse(
        url="/auth/login?prompt=select_account", status_code=status.HTTP_302_FOUND
    )
    response.delete_cookie("session")
    return response


@router.post("/auth/login")
async def password_login(
    session: SessionDep,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: Optional[str] = Form("/"),
):
    """Handle password-based login (only works when AUTH_TYPE='password')."""
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password authentication is not enabled",
        )

    # Validate redirect URL
    if not next_url or not is_safe_redirect_url(next_url, request):
        next_url = "/"

    # Get user by username
    user = await get_user_by_username(session, username)
    if not user or not user.password_hash:
        # Redirect back to login with error
        return RedirectResponse(
            url=f"/auth/login?next={next_url}&error=Invalid username or password",
            status_code=status.HTTP_302_FOUND,
        )

    # Verify password
    if not verify_password(password, user.password_hash, user.id):
        # Redirect back to login with error
        return RedirectResponse(
            url=f"/auth/login?next={next_url}&error=Invalid username or password",
            status_code=status.HTTP_302_FOUND,
        )

    # Create JWT token
    auth_provider = auth_provider_factory()
    if not isinstance(auth_provider, PasswordAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password auth provider not configured correctly",
        )

    jwt_token = auth_provider.create_token(str(user.id))

    # Create session cookie
    session_cookie_value = create_session_cookie(jwt_token)
    response = RedirectResponse(url=next_url, status_code=status.HTTP_302_FOUND)

    response.set_cookie(
        key="session",
        value=session_cookie_value,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
    )

    return response


@router.post("/auth/setup")
async def password_setup(
    session: SessionDep,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    next_url: Optional[str] = Form("/"),
):
    """
    Handle initial admin user setup (only works when no users exist).

    This endpoint only works for the first user (admin setup).
    Once a user exists, registration is disabled.
    """
    if settings.AUTH_TYPE != "password":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password authentication is not enabled",
        )

    # Check if any users exist - only allow setup if no users
    from sqlalchemy import func, select

    from app.models import User

    result = await session.execute(select(func.count(User.id)))
    user_count = result.scalar()

    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup is closed. This instance is in single-organization mode with restricted signup. Please contact your administrator for access.",
        )

    # Validate redirect URL
    if not next_url or not is_safe_redirect_url(next_url, request):
        next_url = "/"

    # Check if passwords match
    if password != confirm_password:
        return RedirectResponse(
            url=f"/auth/login?next={next_url}&error=Passwords do not match",
            status_code=status.HTTP_302_FOUND,
        )

    # Create admin user
    try:
        user = await create_password_user(
            session=session,
            username=username,
            password=password,
        )
    except ValueError as e:
        # User already exists (shouldn't happen, but handle it)
        return RedirectResponse(
            url=f"/auth/login?next={next_url}&error={str(e)}",
            status_code=status.HTTP_302_FOUND,
        )

    # Auto-login: create JWT token
    auth_provider = auth_provider_factory()
    if not isinstance(auth_provider, PasswordAuthProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password auth provider not configured correctly",
        )

    jwt_token = auth_provider.create_token(str(user.id))

    # Create session cookie
    session_cookie_value = create_session_cookie(jwt_token)
    response = RedirectResponse(url=next_url, status_code=status.HTTP_302_FOUND)

    response.set_cookie(
        key="session",
        value=session_cookie_value,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
    )

    return response


def _get_password_login_html(
    next_url: str, error: str = "", is_setup: bool = False
) -> str:
    """
    Generate the HTML for password-based login or setup page.

    Simple, minimal design for self-hosted setups.
    If is_setup=True, shows setup form for first admin user.
    Otherwise, shows login form only (signup is blocked after first user).
    """
    error_html = f'<p style="color: red;">{error}</p>' if error else ""

    if is_setup:
        # First-time setup page
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Floww - Setup</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
        }}
        p {{
            text-align: center;
            color: #666;
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
    </style>
</head>
<body>
    <h1>Floww Setup</h1>
    <p>Create your owner account (first user)</p>
    {error_html}

    <form action="/auth/setup" method="POST">
        <input type="hidden" name="next_url" value="{next_url}">
        <label>Username</label>
        <input type="text" name="username" required autofocus>
        <label>Password</label>
        <input type="password" name="password" required>
        <label>Confirm Password</label>
        <input type="password" name="confirm_password" required>
        <button type="submit">Create Admin Account</button>
    </form>
</body>
</html>
"""
    else:
        # Normal login page
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Floww - Login</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 400px;
            margin: 50px auto;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
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
    </style>
</head>
<body>
    <h1>Floww</h1>
    {error_html}

    <form action="/auth/login" method="POST">
        <input type="hidden" name="next_url" value="{next_url}">
        <label>Username</label>
        <input type="text" name="username" required autofocus>
        <label>Password</label>
        <input type="password" name="password" required>
        <button type="submit">Login</button>
    </form>
</body>
</html>
"""
