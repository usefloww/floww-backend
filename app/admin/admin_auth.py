import urllib.parse

import jwt
from fastapi import Response
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.factories import auth_provider_factory
from app.utils.session import get_jwt_from_session_cookie

ADMIN_USER_IDS = {
    "user_01K6R7KC1974RG2BXD4SE9Q56F",  # Ruben
    "user_01K6QS8VX9CZHP5C3QVMFKW750",  # Toon
}


class AdminAuth(AuthenticationBackend):
    def __init__(self):
        super().__init__("")

    async def login(self, request: Request) -> bool:
        return True

    async def logout(self, request: Request) -> bool:
        # Clear session cookie by redirecting to logout endpoint
        return True

    async def authenticate(self, request: Request):
        # Get session cookie
        session_cookie = request.cookies.get("session")

        if not session_cookie:
            # No session, redirect to login with next parameter
            current_path = str(request.url.path)
            if request.url.query:
                current_path += f"?{request.url.query}"

            login_url = f"/auth/login?next={urllib.parse.quote(current_path)}"
            return RedirectResponse(url=login_url)

        # Extract JWT from session cookie
        jwt_token = get_jwt_from_session_cookie(session_cookie)
        if not jwt_token:
            # Invalid session cookie, redirect to login
            current_path = str(request.url.path)
            if request.url.query:
                current_path += f"?{request.url.query}"

            login_url = f"/auth/login?next={urllib.parse.quote(current_path)}"
            return RedirectResponse(url=login_url)

        try:
            # Validate JWT token using OIDC
            auth_provider = auth_provider_factory()
            user_id = await auth_provider.validate_token(jwt_token)

            # Check if user is an admin
            if user_id not in ADMIN_USER_IDS:
                return Response(
                    content="Access denied: Admin permissions required", status_code=403
                )

            return True

        except jwt.PyJWTError:
            # Invalid JWT, redirect to login
            current_path = str(request.url.path)
            if request.url.query:
                current_path += f"?{request.url.query}"

            login_url = f"/auth/login?next={urllib.parse.quote(current_path)}"
            return RedirectResponse(url=login_url)
        except Exception:
            # Other errors, return 500
            return Response(content="Authentication error", status_code=500)
