from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.packages.runtimes.utils.aws_lambda import (
    deploy_lambda_function,
    get_lambda_deploy_status,
    invoke_lambda_async,
    invoke_lambda_sync,
)

from ..runtime_types import (
    RuntimeConfig,
    RuntimeCreationStatus,
    RuntimeI,
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
        # Check if image_digest is already a full URI (for default runtimes)
        # or just a digest (for regular runtimes)
        if "/" in runtime_config.image_digest:
            # Already a full URI, use directly
            image_uri = runtime_config.image_digest
        else:
            # Construct full ECR image URI for Lambda
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
        payload: dict[str, Any],
    ) -> None:
        """
        Invoke Lambda function with V2 payload format.
        Payload already contains trigger, data, auth_token, execution_id, and providerConfigs.
        """
        event_payload = {
            "type": "invoke_trigger",
            "userCode": user_code,
            **payload,  # Includes trigger, data, auth_token, execution_id, providerConfigs
        }
        invoke_lambda_async(
            self.lambda_client,
            runtime_config.runtime_id,
            event_payload,
        )

    async def get_definitions(
        self,
        runtime_config: RuntimeConfig,
        user_code: dict[str, str],
        provider_configs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Get trigger and provider definitions from user code via Lambda invocation.
        """
        event_payload = {
            "type": "get_definitions",
            "userCode": user_code,
            "providerConfigs": provider_configs,
        }
        result = invoke_lambda_sync(
            self.lambda_client,
            runtime_config.runtime_id,
            event_payload,
        )
        return result

    async def teardown_unused_runtimes(self) -> None:
        pass
