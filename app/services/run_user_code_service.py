from typing import Any

import structlog
from pydantic import BaseModel

from app.models import Runtime
from app.settings import settings
from app.utils.aws_lambda import invoke_lambda_async
from app.utils.docker import get_or_create_container, send_webhook_to_container

logger = structlog.stdlib.get_logger(__name__)


class WebhookPayload(BaseModel):
    method: str
    path: str
    headers: dict
    body: Any
    query: dict
    params: dict


async def run_user_code(
    runtime: Runtime,
    trigger_id: str,
    files: dict[str, str],
    payload: WebhookPayload,
):
    if settings.RUNTIME_TYPE == "lambda":
        event_payload = {
            "userCode": files,
            "triggerType": "webhook",
            "path": payload.path,
            "method": payload.method,
            "headers": payload.headers,
            "body": payload.body,
            "query": payload.params,
        }

        # Invoke Lambda asynchronously
        invoke_result = invoke_lambda_async(
            runtime_id=str(runtime.id),
            event_payload=event_payload,
        )

        if not invoke_result["success"]:
            logger.error(
                "Failed to invoke Lambda",
                trigger_id=trigger_id,
                runtime_id=str(runtime.id),
                error=invoke_result.get("error"),
            )
            return None

        logger.info(
            "Webhook invoked Lambda for trigger",
            trigger_id=trigger_id,
            runtime_id=str(runtime.id),
        )

    elif settings.RUNTIME_TYPE == "docker":
        # Get image hash from runtime config
        image_hash = runtime.config.get("image_hash") if runtime.config else None
        if not image_hash:
            logger.error(
                "No image_hash found in runtime config",
                runtime_id=str(runtime.id),
                trigger_id=trigger_id,
            )
            return None

        try:
            # Get or create container for this runtime
            container_name = await get_or_create_container(
                runtime_id=str(runtime.id),
                image_hash=image_hash,
            )

            # Prepare event payload (same structure as Lambda)
            event_payload = {
                "userCode": files,
                "triggerType": "webhook",
                "path": payload.path,
                "method": payload.method,
                "headers": payload.headers,
                "body": payload.body,
                "query": payload.params,
            }

            # Send webhook payload to container
            response = await send_webhook_to_container(
                runtime_id=str(runtime.id),
                payload=event_payload,
            )

            logger.info(
                "Webhook sent to Docker container",
                trigger_id=trigger_id,
                runtime_id=str(runtime.id),
                container_name=container_name,
            )

            return response

        except Exception as e:
            logger.error(
                "Failed to execute webhook in Docker container",
                trigger_id=trigger_id,
                runtime_id=str(runtime.id),
                error=str(e),
            )
            return None
