from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.factories import auth_provider_factory
from app.models import ApiKey, User
from app.utils.encryption import hash_api_key


async def get_user_from_token(session: AsyncSession, token: str) -> User:
    """Get user from validated JWT token.

    Only looks up existing users - does not create new users.
    Users should be created during the OAuth callback flow where complete
    user information is available.

    Raises:
        HTTPException: If token is invalid or user doesn't exist
    """
    auth_provider = auth_provider_factory()
    token_user = await auth_provider.validate_token(token)

    # Look up existing user by workos_user_id
    result = await session.execute(
        select(User).where(User.workos_user_id == token_user.id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found. Please complete the authentication flow.",
        )

    return user


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
