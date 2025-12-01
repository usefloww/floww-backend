from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

# Initialize structured logger for the lock manager
structured_logger = structlog.stdlib.get_logger(__name__)


@asynccontextmanager
async def advisory_lock(session: AsyncSession, key: int) -> AsyncGenerator[bool, None]:
    """
    Asynchronous context manager for acquiring a single-key (64-bit)
    PostgreSQL Advisory Lock.

    Yields:
        bool: True if the lock was acquired, False otherwise.
    """
    if not 0 <= key <= (2**64 - 1):
        structured_logger.error("Lock key out of 64-bit range.", key=key)
        raise ValueError("Lock key must be a valid 64-bit integer.")

    lock_acquired = False
    conn: Connection = await session.connection()

    try:
        # 1. Attempt to Acquire the Lock (Non-blocking, single 64-bit key)
        sql_acquire_lock = f"SELECT pg_try_advisory_lock({key});"

        result = await conn.execute(sql_acquire_lock)
        # Note: We must fetch the scalar result
        lock_acquired = await result.scalar()

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
                sql_release_lock = f"SELECT pg_advisory_unlock({key});"
                await conn.execute(sql_release_lock)
                structured_logger.debug("Advisory lock released.", lock_key=key)
            except Exception as exc:
                structured_logger.critical(
                    "CRITICAL: Failed to release advisory lock.",
                    lock_key=key,
                    error=str(exc),
                )
