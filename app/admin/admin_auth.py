import urllib.parse
from typing import Optional

import jwt
from fastapi import Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.settings import settings
from app.utils.auth import validate_jwt_token

ADMIN_USER_IDS = {
    "user_01K6R7KC1974RG2BXD4SE9Q56F",  # Ruben
    "user_01K6QS8VX9CZHP5C3QVMFKW750",  # Toon
}

# Session serializer (same as in admin_auth routes)
session_serializer = URLSafeTimedSerializer(settings.SESSION_SECRET_KEY)


def get_jwt_from_session_cookie(cookie_value: str) -> Optional[str]:
    """Extract JWT token from signed session cookie."""
    try:
        # 30 day expiration for session cookies
        return session_serializer.loads(cookie_value, max_age=30 * 24 * 3600)
    except BadSignature:
        return None


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
            # Validate JWT token to ensure it's valid
            await validate_jwt_token(jwt_token)

            # Decode JWT to get email claim (without validation since we already validated above)
            decoded_token = jwt.decode(jwt_token, options={"verify_signature": False})

            user_id = decoded_token.get("sub")

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
