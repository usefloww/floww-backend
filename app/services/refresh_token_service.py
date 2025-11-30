"""
Service for managing refresh tokens.

Handles refresh token creation, validation, and revocation
for long-lived authentication sessions (primarily CLI and API access).
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken

logger = structlog.stdlib.get_logger(__name__)

# Refresh token configuration
REFRESH_TOKEN_LENGTH = 48  # bytes, results in 64-char URL-safe string


def _hash_token(token: str) -> str:
    """
    Hash a refresh token using SHA-256.

    Args:
        token: The plaintext refresh token

    Returns:
        Hexadecimal hash string (64 characters)
    """
    return hashlib.sha256(token.encode()).hexdigest()


def generate_refresh_token() -> str:
    """
    Generate a secure, random refresh token.

    Returns:
        A 64-character URL-safe random string
    """
    return secrets.token_urlsafe(REFRESH_TOKEN_LENGTH)


async def create_refresh_token(
    session: AsyncSession,
    user_id: UUID,
    device_name: Optional[str] = None,
) -> str:
    """
    Create a new refresh token for a user.

    Args:
        session: Database session
        user_id: User UUID
        device_name: Optional device identifier (e.g., "CLI on MacBook")

    Returns:
        The plaintext refresh token (only time it's visible to the user)
    """
    # Generate plaintext token
    plaintext_token = generate_refresh_token()

    # Hash the token for storage
    token_hash = _hash_token(plaintext_token)

    # Create refresh token record
    refresh_token_record = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        device_name=device_name,
    )
    session.add(refresh_token_record)
    await session.commit()

    logger.info(
        "Created refresh token",
        user_id=str(user_id),
        device_name=device_name,
        token_id=str(refresh_token_record.id),
    )

    # Return plaintext token to caller
    return plaintext_token


async def validate_and_update_refresh_token(
    session: AsyncSession, refresh_token: str
) -> Optional[UUID]:
    """
    Validate a refresh token and update its last_used_at timestamp.

    Args:
        session: Database session
        refresh_token: The plaintext refresh token to validate

    Returns:
        User UUID if token is valid, None otherwise
    """
    # Hash the incoming token
    token_hash = _hash_token(refresh_token)

    # Look up token in database
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    token_record = result.scalar_one_or_none()

    if token_record is None:
        logger.warning("Refresh token not found")
        return None

    # Check if token is revoked
    if token_record.revoked_at is not None:
        logger.warning(
            "Refresh token is revoked",
            token_id=str(token_record.id),
            revoked_at=token_record.revoked_at.isoformat(),
        )
        return None

    # Update last_used_at timestamp
    token_record.last_used_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info(
        "Refresh token validated",
        user_id=str(token_record.user_id),
        token_id=str(token_record.id),
    )

    return token_record.user_id


async def revoke_refresh_token(session: AsyncSession, refresh_token: str) -> bool:
    """
    Revoke a refresh token by marking it as revoked.

    Args:
        session: Database session
        refresh_token: The plaintext refresh token to revoke

    Returns:
        True if token was found and revoked, False otherwise
    """
    # Hash the incoming token
    token_hash = _hash_token(refresh_token)

    # Look up token in database
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    token_record = result.scalar_one_or_none()

    if token_record is None:
        logger.warning("Refresh token not found for revocation")
        return False

    # Check if already revoked
    if token_record.revoked_at is not None:
        logger.info(
            "Refresh token already revoked",
            token_id=str(token_record.id),
        )
        return True

    # Revoke the token
    token_record.revoked_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info(
        "Refresh token revoked",
        user_id=str(token_record.user_id),
        token_id=str(token_record.id),
    )

    return True


async def revoke_all_user_tokens(session: AsyncSession, user_id: UUID) -> int:
    """
    Revoke all active refresh tokens for a user.

    Useful for "logout everywhere" functionality.

    Args:
        session: Database session
        user_id: User UUID

    Returns:
        Number of tokens revoked
    """
    # Get all non-revoked tokens for the user
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    active_tokens = result.scalars().all()

    # Revoke all active tokens
    now = datetime.now(timezone.utc)
    count = 0
    for token in active_tokens:
        token.revoked_at = now
        count += 1

    await session.commit()

    logger.info(
        "Revoked all user refresh tokens",
        user_id=str(user_id),
        count=count,
    )

    return count


async def cleanup_revoked_tokens(
    session: AsyncSession,
    days_old: int = 90,
) -> int:
    """
    Delete revoked refresh tokens older than specified days.

    This is a cleanup function to prevent database bloat.

    Args:
        session: Database session
        days_old: Delete revoked tokens older than this many days

    Returns:
        Number of tokens deleted
    """
    from datetime import timedelta

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)

    # Get revoked tokens older than cutoff
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.revoked_at.isnot(None),
            RefreshToken.revoked_at < cutoff_date,
        )
    )
    old_tokens = result.scalars().all()

    # Delete them
    for token in old_tokens:
        await session.delete(token)

    await session.commit()

    logger.info(
        "Cleaned up old revoked refresh tokens",
        count=len(old_tokens),
        days_old=days_old,
    )

    return len(old_tokens)
