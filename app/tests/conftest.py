import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.deps.db import get_async_db
from app.main import app  # Replace with your actual FastAPI app
from app.models import Base  # Replace with your actual imports
from app.settings import settings


def get_test_database_url():
    """
    Create a test database URL with a separate test database name.
    """
    test_db_name = f"{settings.DB_NAME}_test"
    return f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{test_db_name}"


def get_admin_database_url():
    """
    Create a database URL for admin operations (creating/dropping test database).
    Uses the default postgres database for admin operations.
    """
    return f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/postgres"


@pytest.fixture(scope="session")
async def setup_test_database():
    """
    Create the test database before running tests and drop it after.
    """
    test_db_name = f"{settings.DB_NAME}_test"
    admin_engine = create_async_engine(
        get_admin_database_url(), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )

    try:
        # Create test database
        async with admin_engine.connect() as conn:
            # Check if database exists
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                {"db_name": test_db_name},
            )
            if not result.fetchone():
                await conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))

        yield

        # Drop test database after all tests
        async with admin_engine.connect() as conn:
            # Terminate all connections to the test database
            await conn.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :db_name AND pid <> pg_backend_pid()
                """
                ),
                {"db_name": test_db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))

    finally:
        await admin_engine.dispose()


@pytest.fixture(scope="session")
async def db_engine(setup_test_database):
    """
    Create an async engine and test database schema.
    """
    engine = create_async_engine(get_test_database_url(), poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture(scope="function")
async def session(db_engine):
    """
    Create an async session with a rollback after the test.
    """
    async with db_engine.connect() as connection:
        async with connection.begin():
            session = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                class_=AsyncSession,
            )()

            try:
                yield session
            finally:
                await session.rollback()
                await session.close()


@pytest.fixture(scope="function")
async def client(session: AsyncSession):
    """
    Provide a test client with the async DB session dependency overridden.
    """

    async def override_get_db():
        yield session

    app.dependency_overrides[get_async_db] = override_get_db

    async with AsyncClient(app=app, base_url="http://localhost") as ac:
        yield ac
