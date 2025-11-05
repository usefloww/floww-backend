import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature

from app.factories import get_auth_provider
from app.utils.session import (
    create_session_cookie,
    get_jwt_from_session_cookie,
    is_safe_redirect_url,
    session_serializer,
)

router = APIRouter(tags=["Admin Auth"])


@router.get("/auth/login")
async def admin_login(
    request: Request,
    next_url: Optional[str] = Query(None, alias="next"),
    prompt: Optional[str] = Query(None),
):
    if not next_url or not is_safe_redirect_url(next_url, request):
        next_url = "/"

    state = secrets.token_urlsafe(32)
    state_data = {"csrf": state, "next": next_url}
    signed_state = session_serializer.dumps(state_data)

    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/auth/callback"

    auth_provider = get_auth_provider()

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

    auth_provider = get_auth_provider()
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
async def admin_logout(request: Request):
    # Extract session token to revoke it
    session_cookie = request.cookies.get("session")
    if session_cookie:
        jwt_token = get_jwt_from_session_cookie(session_cookie)
        if jwt_token:
            try:
                auth_provider = get_auth_provider()
                await auth_provider.revoke_session(jwt_token)
            except Exception as e:
                # Log but don't fail logout if revocation fails
                print(f"Session revocation error: {e}")

    response = RedirectResponse(
        url="/auth/login?prompt=select_account", status_code=status.HTTP_302_FOUND
    )
    response.delete_cookie("session")
    return response
