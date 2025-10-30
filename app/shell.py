import asyncio
from typing import Annotated, AsyncGenerator  # noqa

from fastapi import Depends  # noqa
from sqlalchemy import *  # type: ignore # noqa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.deps.db import AsyncSessionLocal
from app.models import *  # noqa
from app.settings import settings

# Create async engine
engine = create_async_engine(settings.DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Create a session
async def get_session():
    async with AsyncSessionLocal() as session:
        return session


# Get the session
session = asyncio.run(get_session())
