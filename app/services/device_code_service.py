"""
Service for managing OAuth2 device authorization flow.

Handles device code generation, verification, and token exchange
for CLI and other device authentication.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeviceCode, DeviceCodeStatus


class DeviceAuthorizationData(TypedDict):
    """Data returned when initiating device authorization."""

    device_code: str
    user_code: str
    expires_in: int
    interval: int


logger = structlog.stdlib.get_logger(__name__)

# Device code configuration
DEVICE_CODE_LENGTH = 43  # URL-safe base64 encoding of 32 bytes
USER_CODE_LENGTH = 8  # Human-readable format: XXXX-XXXX
DEVICE_CODE_EXPIRY_SECONDS = 900  # 15 minutes
POLL_INTERVAL_SECONDS = 5  # Minimum polling interval


def generate_device_code() -> str:
    """
    Generate a secure, random device code.
    Returns a 43-character URL-safe string (base64 encoding of 32 random bytes).
    """
    return secrets.token_urlsafe(32)


def generate_user_code() -> str:
    """
    Generate a human-readable user code in format XXXX-XXXX.
    Uses uppercase letters and digits (excluding confusing characters like 0, O, 1, I).
    """
    # Character set excluding confusing characters
    charset = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    code_part1 = "".join(secrets.choice(charset) for _ in range(4))
    code_part2 = "".join(secrets.choice(charset) for _ in range(4))
    return f"{code_part1}-{code_part2}"


async def create_device_authorization(
    session: AsyncSession,
) -> DeviceAuthorizationData:
    """
    Create a new device authorization request.

    Returns a dict with device_code, user_code, expires_in, and interval.
    """
    device_code = generate_device_code()
    user_code = generate_user_code()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=DEVICE_CODE_EXPIRY_SECONDS
    )

    # Ensure user_code is unique (very unlikely collision, but check anyway)
    max_attempts = 10
    for _ in range(max_attempts):
        result = await session.execute(
            select(DeviceCode).where(DeviceCode.user_code == user_code)
        )
        if result.scalar_one_or_none() is None:
            break
        user_code = generate_user_code()
    else:
        raise RuntimeError(
            "Failed to generate unique user code after multiple attempts"
        )

    # Create device code record
    device_code_record = DeviceCode(
        device_code=device_code,
        user_code=user_code,
        status=DeviceCodeStatus.PENDING,
        expires_at=expires_at,
    )
    session.add(device_code_record)
    await session.commit()

    logger.info(
        "Created device authorization",
        user_code=user_code,
        expires_at=expires_at.isoformat(),
    )

    return {
        "device_code": device_code,
        "user_code": user_code,
        "expires_in": DEVICE_CODE_EXPIRY_SECONDS,
        "interval": POLL_INTERVAL_SECONDS,
    }


async def get_device_code_by_user_code(
    session: AsyncSession, user_code: str
) -> Optional[DeviceCode]:
    """
    Retrieve a device code record by user code.
    """
    result = await session.execute(
        select(DeviceCode).where(DeviceCode.user_code == user_code.upper())
    )
    return result.scalar_one_or_none()


async def get_device_code_by_device_code(
    session: AsyncSession, device_code: str
) -> Optional[DeviceCode]:
    """
    Retrieve a device code record by device code.
    """
    result = await session.execute(
        select(DeviceCode).where(DeviceCode.device_code == device_code)
    )
    return result.scalar_one_or_none()


async def approve_device_code(
    session: AsyncSession, user_code: str, user_id: UUID
) -> bool:
    """
    Approve a device code for a specific user.

    Returns True if successful, False if the code doesn't exist or is expired.
    """
    device_code_record = await get_device_code_by_user_code(session, user_code)

    if device_code_record is None:
        logger.warning("Device code not found", user_code=user_code)
        return False

    # Check if expired
    if datetime.now(timezone.utc) > device_code_record.expires_at:
        device_code_record.status = DeviceCodeStatus.EXPIRED
        await session.commit()
        logger.warning("Device code expired", user_code=user_code)
        return False

    # Check if already used
    if device_code_record.status != DeviceCodeStatus.PENDING:
        logger.warning(
            "Device code already used",
            user_code=user_code,
            status=device_code_record.status,
        )
        return False

    # Approve the device code
    device_code_record.status = DeviceCodeStatus.APPROVED
    device_code_record.user_id = user_id
    await session.commit()

    logger.info("Device code approved", user_code=user_code, user_id=str(user_id))
    return True


async def deny_device_code(session: AsyncSession, user_code: str) -> bool:
    """
    Deny a device code request.

    Returns True if successful, False if the code doesn't exist.
    """
    device_code_record = await get_device_code_by_user_code(session, user_code)

    if device_code_record is None:
        return False

    device_code_record.status = DeviceCodeStatus.DENIED
    await session.commit()

    logger.info("Device code denied", user_code=user_code)
    return True


async def check_device_code_status(
    session: AsyncSession, device_code: str
) -> tuple[DeviceCodeStatus, Optional[UUID]]:
    """
    Check the status of a device code for polling.

    Returns a tuple of (status, user_id).
    - PENDING: Authorization still pending
    - APPROVED: User authorized, user_id is set
    - DENIED: User denied authorization
    - EXPIRED: Code expired
    """
    device_code_record = await get_device_code_by_device_code(session, device_code)

    if device_code_record is None:
        # Invalid device code - treat as expired
        return (DeviceCodeStatus.EXPIRED, None)

    # Check if expired
    if (
        datetime.now(timezone.utc) > device_code_record.expires_at
        and device_code_record.status == DeviceCodeStatus.PENDING
    ):
        device_code_record.status = DeviceCodeStatus.EXPIRED
        await session.commit()
        return (DeviceCodeStatus.EXPIRED, None)

    return (device_code_record.status, device_code_record.user_id)


async def delete_device_code(session: AsyncSession, device_code: str) -> None:
    """
    Delete a device code record (e.g., after successful token exchange).
    """
    device_code_record = await get_device_code_by_device_code(session, device_code)
    if device_code_record is not None:
        await session.delete(device_code_record)
        await session.commit()
        logger.info("Device code deleted", device_code=device_code[:10] + "...")


async def cleanup_expired_device_codes(session: AsyncSession) -> int:
    """
    Delete all expired device codes.

    Returns the number of deleted records.
    """
    result = await session.execute(
        select(DeviceCode).where(DeviceCode.expires_at < datetime.now(timezone.utc))
    )
    expired_codes = result.scalars().all()

    for code in expired_codes:
        await session.delete(code)

    await session.commit()

    logger.info("Cleaned up expired device codes", count=len(expired_codes))
    return len(expired_codes)
