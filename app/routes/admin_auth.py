import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature

from app.utils.session import (
    create_session_cookie,
    is_safe_redirect_url,
    session_serializer,
)

router = APIRouter(tags=["Admin Auth"])


@router.get("/auth/login")
async def admin_login(
    request: Request,
    next_url: Optional[str] = Query(None, alias="next"),
):
    from app.auth.oidc import get_authorization_url

    if next_url and not is_safe_redirect_url(next_url, request):
        next_url = "/admin"
    elif not next_url:
        next_url = "/admin"

    state = secrets.token_urlsafe(32)
    state_data = {"csrf": state, "next": next_url}
    signed_state = session_serializer.dumps(state_data)

    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/auth/callback"

    auth_url = await get_authorization_url(
        redirect_uri=redirect_uri,
        state=signed_state,
        scope="openid profile email",
    )

    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/callback")
async def admin_auth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    from app.auth.oidc import exchange_code_for_token

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
        next_url = state_data.get("next", "/admin")
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter",
        )

    host = request.headers.get("host")
    scheme = "http" if host and "localhost" in host else "https"
    redirect_uri = f"{scheme}://{host}/auth/callback"

    token_data = await exchange_code_for_token(code, redirect_uri)
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
async def admin_logout():
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("session")
    return response
