from sqlalchemy.orm import Session

from app.factories import get_auth_provider
from app.models import User
from app.services.user_service import get_or_create_user


async def get_user_from_token(session: Session, token: str) -> User:
    auth_provider = get_auth_provider()
    external_user_id = await auth_provider.validate_token(token)

    return await get_or_create_user(session, external_user_id)


def get_user_from_cookie(token: str) -> User:
    import asyncio

    from app.deps.db import get_session

    session = next(get_session())

    try:
        return asyncio.run(get_user_from_token(session, token))
    finally:
        session.close()
