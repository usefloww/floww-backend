import os

import sentry_sdk
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routes import (
    admin_auth,
    centrifugo,
    config,
    dev,
    docker_proxy,
    health,
    kv_store,
    namespaces,
    organizations,
    provider_types,
    providers,
    runtimes,
    secrets,
    service_accounts,
    webhooks,
    whoami,
    workflow_deployments,
    workflows,
)
from app.routes.admin import init_admin
from app.settings import settings
from app.utils.logging_utils import setup_logger


def init_sentry():
    environment = os.getenv("SENTRY_ENVIRONMENT", default="")
    if environment not in {"production", "staging"}:
        dsn = ""
    else:
        dsn = os.getenv("SENTRY_DSN", default="")

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.01,
        profiles_sample_rate=0.01,
        enable_tracing=True,
        send_default_pii=False,
    )


app = FastAPI()

setup_logger(app)

init_admin(app)


@app.on_event("startup")
async def startup_event():
    """Initialize single-org mode if enabled."""
    if settings.SINGLE_ORG_MODE:
        from app.deps.db import AsyncSessionLocal
        from app.utils.single_org import ensure_default_organization

        async with AsyncSessionLocal() as session:
            try:
                org_id, namespace_id = await ensure_default_organization(session)
                # Store in app state for easy access
                app.state.default_organization_id = org_id
                app.state.default_namespace_id = namespace_id
                print(
                    f"Single-org mode initialized: org_id={org_id}, namespace_id={namespace_id}"
                )

                # Initialize anonymous user if AUTH_TYPE='none'
                if settings.AUTH_TYPE == "none":
                    from sqlalchemy import select

                    from app.models import OrganizationMember, OrganizationRole, User

                    # Check if anonymous user already exists
                    result = await session.execute(
                        select(User).where(User.workos_user_id == "anonymous")
                    )
                    anonymous_user = result.scalar_one_or_none()

                    if anonymous_user is None:
                        # Create anonymous user
                        anonymous_user = User(
                            workos_user_id="anonymous",
                            email="anonymous@floww.local",
                            first_name="Anonymous",
                            last_name="User",
                        )
                        session.add(anonymous_user)
                        await session.flush()

                        # Add anonymous user to default organization as admin
                        org_member = OrganizationMember(
                            organization_id=org_id,
                            user_id=anonymous_user.id,
                            role=OrganizationRole.ADMIN,
                        )
                        session.add(org_member)
                        await session.commit()
                        print(
                            f"Anonymous user created: user_id={anonymous_user.id}, role=admin"
                        )
                    else:
                        # Verify anonymous user is member of default organization
                        result = await session.execute(
                            select(OrganizationMember).where(
                                OrganizationMember.organization_id == org_id,
                                OrganizationMember.user_id == anonymous_user.id,
                            )
                        )
                        membership = result.scalar_one_or_none()

                        if membership is None:
                            # Add to organization if not already a member
                            org_member = OrganizationMember(
                                organization_id=org_id,
                                user_id=anonymous_user.id,
                                role=OrganizationRole.ADMIN,
                            )
                            session.add(org_member)
                            await session.commit()
                            print(
                                f"Anonymous user added to default organization: user_id={anonymous_user.id}"
                            )

                    # Store anonymous user ID in app state
                    app.state.anonymous_user_id = anonymous_user.id
                    print(
                        f"Anonymous authentication enabled: user_id={anonymous_user.id}"
                    )

            finally:
                await session.close()


api_router = APIRouter(prefix="/api")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"title": "Validation error", "description": exc.errors()},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"title": "Internal Server Error", "description": str(exc)},
    )


api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(workflows.router)
api_router.include_router(whoami.router)
api_router.include_router(secrets.router)
api_router.include_router(runtimes.router)
api_router.include_router(namespaces.router)
api_router.include_router(providers.router)
api_router.include_router(provider_types.router)
api_router.include_router(workflow_deployments.router)
api_router.include_router(organizations.router)
api_router.include_router(dev.router)
api_router.include_router(service_accounts.router)
api_router.include_router(kv_store.router)
app.include_router(docker_proxy.router)
app.include_router(webhooks.router)
app.include_router(admin_auth.router)
app.include_router(centrifugo.router)
app.include_router(api_router)
