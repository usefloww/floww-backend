import os

import sentry_sdk
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routes import admin_auth, health, secrets, webhooks, whoami, workflows, centrifugo
from app.routes.admin import init_admin
from app.utils.logging import setup_logger


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
api_router.include_router(workflows.router)
api_router.include_router(whoami.router)
api_router.include_router(secrets.router)
app.include_router(webhooks.router)
app.include_router(admin_auth.router)
app.include_router(centrifugo.router)
app.include_router(api_router)
