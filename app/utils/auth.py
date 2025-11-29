from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.factories import auth_provider_factory
from app.models import ApiKey, User
from app.services.user_service import get_or_create_user
from app.utils.encryption import hash_api_key


async def get_user_from_token(session: AsyncSession, token: str) -> User:
    auth_provider = auth_provider_factory()
    token_user = await auth_provider.validate_token(token)

    # Extract user information from TokenUser and pass to get_or_create_user
    return await get_or_create_user(
        session,
        workos_user_id=token_user.sub,
        email=token_user.email,
        first_name=token_user.given_name,
        last_name=token_user.family_name,
    )


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
