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


async def execute_sql(sql: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql))  # noqa: F405
        await session.commit()

        if not result.returns_rows:
            return

        rows = result.fetchall()
        cols = result.keys()

    # compute column widths
    col_widths = {c: len(c) for c in cols}
    for row in rows:
        for c, v in zip(cols, row):
            col_widths[c] = max(col_widths[c], len(str(v)))

    # build header
    header = " | ".join(c.ljust(col_widths[c]) for c in cols)
    sep = "-+-".join("-" * col_widths[c] for c in cols)

    print(header)
    print(sep)

    # build rows
    for row in rows:
        line = " | ".join(str(v).ljust(col_widths[c]) for c, v in zip(cols, row))
        print(line)


# Get the session
session = asyncio.run(get_session())
