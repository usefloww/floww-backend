import pytest
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps.auth import get_current_user, get_current_user_optional
from app.deps.db import get_async_db, get_committed_db
from app.main import app
from app.models import Namespace, Organization, OrganizationMember, User
from app.services.user_service import get_or_create_user


class UserClient(AsyncClient):
    user: User
    namespace: Namespace
    organization: Organization


def _client_args(session):
    async def commit(response):
        session.expunge_all()

    return {
        "app": app,
        "base_url": "http://localhost",
        "event_hooks": {"response": [commit]},
    }


async def _get_user_namespace(session: AsyncSession, user: User) -> Namespace:
    """Get the namespace for the user's first organization."""
    # Find the user's first organization
    org_member_query = (
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at)
        .limit(1)
    )
    org_member_result = await session.execute(org_member_query)
    org_member = org_member_result.scalar_one_or_none()

    if not org_member:
        return None

    # Get the namespace for that organization
    namespace_query = (
        select(Namespace)
        .options(selectinload(Namespace.organization_owner))
        .where(Namespace.organization_owner_id == org_member.organization_id)
    )
    namespace_result = await session.execute(namespace_query)
    return namespace_result.scalar_one_or_none()


async def _get_user_organization(session: AsyncSession, user: User) -> Organization:
    """Get the user's first organization."""
    org_query = (
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at)
        .limit(1)
    )
    org_result = await session.execute(org_query)
    return org_result.scalar_one_or_none()


async def mock_get_current_user(
    request: Request,
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

    if token.startswith("test_"):
        return await get_or_create_user(session, token)

    return await get_current_user(request, session, credentials)


@pytest.fixture(scope="function")
async def dependency_overrides(session: AsyncSession):
    async def override_get_db():
        yield session

    app.dependency_overrides[get_async_db] = override_get_db
    app.dependency_overrides[get_committed_db] = override_get_db
    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[get_current_user_optional] = mock_get_current_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def client(session: AsyncSession, dependency_overrides):
    """Base test client with DB and auth overrides."""

    async with AsyncClient(**_client_args(session)) as ac:
        yield ac


@pytest.fixture(scope="function")
async def client_a(session, dependency_overrides):
    async with AsyncClient(**_client_args(session)) as ac:
        ac.user = await get_or_create_user(session, "test_user_a", create=False)
        ac.namespace = await _get_user_namespace(session, ac.user)
        ac.organization = await _get_user_organization(session, ac.user)

        ac.headers["Authorization"] = "Bearer test_user_a"
        yield ac


@pytest.fixture(scope="function")
async def client_b(session, dependency_overrides):
    async with AsyncClient(**_client_args(session)) as ac:
        ac.user = await get_or_create_user(session, "test_user_b", create=False)
        ac.namespace = await _get_user_namespace(session, ac.user)
        ac.organization = await _get_user_organization(session, ac.user)

        ac.headers["Authorization"] = "Bearer test_user_b"
        yield ac
