import logging
import time
from enum import Enum

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from pydantic_settings import BaseSettings
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from structlog.typing import Processor
from uvicorn.protocols.utils import get_path_with_query_string


class LogFormats(str, Enum):
    JSON = "json"
    CONSOLE = "console"


class LogSettings(BaseSettings):
    log_format: LogFormats = LogFormats.JSON
    log_level: str = "INFO"


def setup_logger(app: FastAPI):
    settings = LogSettings()
    log_level = settings.log_level

    log_renderer: Processor
    if settings.log_format == LogFormats.CONSOLE:
        log_renderer = structlog.dev.ConsoleRenderer()
    else:
        log_renderer = structlog.processors.JSONRenderer()

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.StackInfoRenderer(),
        structlog.stdlib.ExtraAdder(),
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.PATHNAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            ],
        ),
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.log_format == LogFormats.JSON:
        processors.append(structlog.processors.format_exc_info)

    structlog.configure(
        processors=processors  # type: ignore
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=processors,  # type: ignore
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            log_renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    for _log in ["uvicorn", "uvicorn.error"]:
        logging.getLogger(_log).handlers.clear()
        logging.getLogger(_log).propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CorrelationIdMiddleware)


access_logger = structlog.stdlib.get_logger("api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        request_id = correlation_id.get()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start_time = time.perf_counter()
        response = Response(status_code=500)
        try:
            response: StreamingResponse = await call_next(request)
        except Exception:
            structlog.stdlib.get_logger("api.error").exception("Uncaught exception")
            raise
        finally:
            process_time = time.perf_counter() - start_time
            status_code = response.status_code
            url = get_path_with_query_string(request.scope)  # type: ignore
            endpoint = request.url.path
            http_method = request.method
            http_version = request.scope["http_version"]
            access_logger.info(
                f""""{http_method} {url} HTTP/{http_version}" {status_code}""",
                http={
                    "url": str(request.url),
                    "status_code": status_code,
                    "method": http_method,
                    "request_id": request_id,
                    "version": http_version,
                },
                duration=process_time,
                endpoint=endpoint,
                query_params=dict(request.query_params),
            )
            response.headers["X-Process-Time"] = str(process_time / 10**9)
            return response
