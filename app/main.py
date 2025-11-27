from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.factories import scheduler_factory
from app.routes import (
    admin_auth,
    billing,
    centrifugo,
    config,
    dev,
    docker_proxy,
    executions,
    health,
    kv_store,
    namespaces,
    organizations,
    provider_types,
    providers,
    runtimes,
    secrets,
    service_accounts,
    subscriptions,
    summary,
    webhooks,
    whoami,
    workflow_deployments,
    workflows,
)
from app.routes.admin import init_admin
from app.settings import settings
from app.utils.logging_utils import setup_logger_fastapi
from app.utils.migrations import run_migrations
from app.utils.sentry import init_sentry
from app.utils.single_org import setup_single_org_mode


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        await run_migrations()

    if settings.SINGLE_ORG_MODE:
        await setup_single_org_mode(app)

    if settings.SCHEDULER_ENABLED:
        scheduler = scheduler_factory()
        scheduler.start()

    yield

    scheduler = scheduler_factory()
    scheduler.shutdown(wait=True)


init_sentry()
app = FastAPI(lifespan=lifespan)
setup_logger_fastapi(app)
init_admin(app)


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
api_router.include_router(executions.router)
api_router.include_router(subscriptions.router)
api_router.include_router(billing.router)
api_router.include_router(summary.router)
app.include_router(docker_proxy.router)
app.include_router(webhooks.router)
app.include_router(admin_auth.router)
app.include_router(centrifugo.router)
app.include_router(api_router)
