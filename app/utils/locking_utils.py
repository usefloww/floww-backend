from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# Initialize structured logger for the lock manager
structured_logger = structlog.stdlib.get_logger(__name__)


@asynccontextmanager
async def advisory_lock(session: AsyncSession, key: int) -> AsyncGenerator[bool, None]:
    """
    Asynchronous context manager for acquiring a single-key (64-bit)
    PostgreSQL Advisory Lock.

    Note: PostgreSQL BIGINT is signed, so the key must be in range [0, 2^63-1].

    Yields:
        bool: True if the lock was acquired, False otherwise.
    """
    if not 0 <= key <= (2**63 - 1):
        structured_logger.error("Lock key out of signed 64-bit range.", key=key)
        raise ValueError(
            "Lock key must be a valid signed 64-bit integer (0 to 2^63-1)."
        )

    lock_acquired = False
    conn: AsyncConnection = await session.connection()

    try:
        # 1. Attempt to Acquire the Lock (Non-blocking, single 64-bit key)
        sql_acquire_lock = text("SELECT pg_try_advisory_lock(:key)")

        result = await conn.execute(sql_acquire_lock, {"key": key})
        # Note: We must fetch the scalar result
        lock_acquired = result.scalar()

        if lock_acquired:
            structured_logger.debug("Advisory lock acquired.", lock_key=key)
            yield True
        else:
            structured_logger.info(
                "Lock already held by another worker for this run key. Skipping.",
                lock_key=key,
            )
            yield False

    except Exception as exc:
        structured_logger.error(
            "Advisory lock operation failed.",
            lock_key=key,
            error=str(exc),
            exc_info=True,
        )
        yield False
        raise

    finally:
        # 2. Release the Lock (If acquired)
        if lock_acquired:
            try:
                # Check if connection is still valid before attempting to release
                if conn.closed:
                    structured_logger.debug(
                        "Connection already closed, lock will be released automatically.",
                        lock_key=key,
                    )
                else:
                    sql_release_lock = text("SELECT pg_advisory_unlock(:key)")
                    await conn.execute(sql_release_lock, {"key": key})
                    structured_logger.debug("Advisory lock released.", lock_key=key)
            except Exception as exc:
                # If connection is closed, the lock will be released automatically
                # when the connection closes, so this is not critical
                if "closed" in str(exc).lower() or conn.closed:
                    structured_logger.debug(
                        "Connection closed before lock release (lock will auto-release).",
                        lock_key=key,
                    )
                else:
                    structured_logger.critical(
                        "CRITICAL: Failed to release advisory lock.",
                        lock_key=key,
                        error=str(exc),
                    )
