import urllib.parse
from typing import Optional

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.settings import settings

session_serializer = URLSafeTimedSerializer(settings.SESSION_SECRET_KEY)


def create_session_cookie(jwt_token: str) -> str:
    return session_serializer.dumps(jwt_token)


def get_jwt_from_session_cookie(cookie_value: str) -> Optional[str]:
    try:
        return session_serializer.loads(cookie_value, max_age=30 * 24 * 3600)
    except BadSignature:
        return None


def is_safe_redirect_url(url: str, request: Request) -> bool:
    """Check if redirect URL is safe to prevent open redirects."""
    if not url:
        return False

    parsed_url = urllib.parse.urlparse(url)
    request_host = request.headers.get("host", "")

    # Allow all relative URLs (they can't be open redirects)
    if not parsed_url.netloc:
        return url.startswith("/")

    # For absolute URLs, only allow same host
    return parsed_url.netloc.lower() == request_host.lower()
