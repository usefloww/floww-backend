from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.packages.runtimes.utils.aws_lambda import (
    deploy_lambda_function,
    get_lambda_deploy_status,
    invoke_lambda_async,
)

from ..runtime_types import (
    RuntimeConfig,
    RuntimeCreationStatus,
    RuntimeI,
    RuntimeWebhookPayload,
)

if TYPE_CHECKING:
    from mypy_boto3_lambda.client import LambdaClient


class LambdaRuntime(RuntimeI):
    def __init__(self, lambda_client: "LambdaClient", execution_role_arn: str):
        self.lambda_client = lambda_client
        self.execution_role_arn = execution_role_arn

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        deploy_lambda_function(
            self.lambda_client,
            runtime_config.runtime_id,
            runtime_config.image_uri,
            self.execution_role_arn,
        )
        return RuntimeCreationStatus(
            status="in_progress",
            new_logs=[
                {
                    "timestamp": str(datetime.now(timezone.utc)),
                    "message": "Lambda deployment initiated",
                    "level": "info",
                }
            ],
        )

    async def get_runtime_status(
        self,
        runtime_id: str,
    ) -> RuntimeCreationStatus:
        status = get_lambda_deploy_status(self.lambda_client, runtime_id)
        return RuntimeCreationStatus(
            status=status["status"].lower(),
            new_logs=[
                {
                    "timestamp": str(datetime.now(timezone.utc)),
                    "message": status["logs"],
                }
            ],
        )

    async def invoke_trigger(
        self,
        trigger_id: str,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        payload: RuntimeWebhookPayload,
    ) -> None:
        invoke_lambda_async(
            self.lambda_client,
            runtime_config.runtime_id,
            payload.model_dump(),
        )
