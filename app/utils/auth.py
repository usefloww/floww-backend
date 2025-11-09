from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.factories import auth_provider_factory
from app.models import ApiKey, User
from app.services.user_service import get_or_create_user
from app.utils.encryption import hash_api_key


async def get_user_from_token(session: AsyncSession, token: str) -> User:
    auth_provider = auth_provider_factory()
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


async def get_user_from_api_key(session: AsyncSession, api_key: str) -> User:
    """Validate an API key and return the associated user.

    Args:
        session: Database session
        api_key: API key string (should start with 'floww_sa_')

    Returns:
        User object associated with the API key

    Raises:
        HTTPException: If the API key is invalid or revoked
    """
    # Hash the API key
    hashed_key = hash_api_key(api_key)

    # Look up the API key in the database
    api_key_query = select(ApiKey).where(ApiKey.hashed_key == hashed_key)
    result = await session.execute(api_key_query)
    api_key_obj = result.scalar_one_or_none()

    # Verify it exists and is not revoked
    if not api_key_obj or api_key_obj.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # Load the user (service account)
    user_query = select(User).where(User.id == api_key_obj.user_id)
    user_result = await session.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return user
