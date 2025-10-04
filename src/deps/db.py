from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.settings import settings

async_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=pool.NullPool,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_async_db)]
