import pytest
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.auth import get_current_user
from app.deps.db import get_async_db
from app.main import app
from app.models import Namespace, User
from app.services.user_service import get_or_create_user


class UserClient(AsyncClient):
    user: User
    personal_namespace: Namespace


async def _get_personal_namespace(session: AsyncSession, user: User) -> Namespace:
    personal_namespace_query = select(Namespace).where(
        Namespace.user_owner_id == user.id
    )
    personal_namespace_result = await session.execute(personal_namespace_query)
    return personal_namespace_result.scalar_one_or_none()


async def mock_get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
    session=Depends(get_async_db),
) -> User:
    """Mock get_current_user that maps test tokens to users, falls back to original for real tokens."""

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    if token.startswith("test_user"):
        return await get_or_create_user(session, token)

    return await get_current_user(credentials, session)


@pytest.fixture(scope="function")
async def dependency_overrides(session: AsyncSession):
    async def override_get_db():
        yield session

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_current_user] = mock_get_current_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def client(session: AsyncSession, dependency_overrides):
    """Base test client with DB and auth overrides."""

    async with AsyncClient(app=app, base_url="http://localhost") as ac:
        yield ac


@pytest.fixture(scope="function")
async def client_a(session, dependency_overrides):
    async with AsyncClient(app=app, base_url="http://localhost") as ac:
        ac.user = await get_or_create_user(session, "test_user_a")
        ac.personal_namespace = await _get_personal_namespace(session, ac.user)
        ac.headers["Authorization"] = "Bearer test_user_a"
        yield ac


@pytest.fixture(scope="function")
async def client_b(session, dependency_overrides):
    async with AsyncClient(app=app, base_url="http://localhost") as ac:
        ac.user = await get_or_create_user(session, "test_user_b")
        ac.personal_namespace = await _get_personal_namespace(session, ac.user)
        ac.headers["Authorization"] = "Bearer test_user_b"
        yield ac
