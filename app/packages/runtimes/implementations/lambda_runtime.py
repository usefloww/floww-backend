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
    def __init__(
        self,
        lambda_client: "LambdaClient",
        execution_role_arn: str,
        registry_url: str,
        repository_name: str,
        backend_url: str,
    ):
        self.lambda_client = lambda_client
        self.execution_role_arn = execution_role_arn
        self.registry_url = registry_url
        self.repository_name = repository_name
        self.backend_url = backend_url

    async def create_runtime(
        self,
        runtime_config: RuntimeConfig,
    ) -> RuntimeCreationStatus:
        # Construct full ECR image URI for Lambda
        # Extract registry host from REGISTRY_URL
        registry_url = self.registry_url
        if "/" in registry_url:
            registry_host = registry_url.split("/")[0]
        else:
            registry_host = registry_url

        image_uri = (
            f"{registry_host}/{self.repository_name}@{runtime_config.image_digest}"
        )

        deploy_lambda_function(
            self.lambda_client,
            runtime_config.runtime_id,
            image_uri,
            self.execution_role_arn,
            backend_url=self.backend_url,
        )
        return RuntimeCreationStatus(
            status="IN_PROGRESS",
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
            status=status["status"],
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
        provider_configs: dict[str, dict[str, str]] | None = None,
    ) -> None:
        event_payload = {
            **payload.model_dump(),
            "userCode": user_code,
            "triggerType": "webhook",
            "providerConfigs": provider_configs or {},
        }
        invoke_lambda_async(
            self.lambda_client,
            runtime_config.runtime_id,
            event_payload,
        )
