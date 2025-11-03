from sqlalchemy.orm import Session

from app.auth.oidc import validate_jwt
from app.models import User
from app.services.user_service import get_or_create_user
from app.settings import settings


async def get_user_from_token(session: Session, token: str) -> User:
    external_user_id = await validate_jwt(
        token, settings.AUTH_ISSUER_URL, settings.AUTH_CLIENT_ID
    )

    return await get_or_create_user(session, external_user_id)


def get_user_from_cookie(token: str) -> User:
    import asyncio

    from app.deps.db import get_session

    session = next(get_session())

    try:
        return asyncio.run(get_user_from_token(session, token))
    finally:
        session.close()
