from typing import TYPE_CHECKING

import structlog
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_lambda.client import LambdaClient

logger = structlog.stdlib.get_logger(__name__)


def deploy_lambda_function(
    lambda_client: "LambdaClient",
    runtime_id: str,
    image_uri: str,
    execution_role_arn: str,
):
    """Deploy a Lambda function with container image."""
    function_name = f"floww-runtime-{runtime_id}"

    lambda_client.create_function(
        FunctionName=function_name,
        Role=execution_role_arn,
        Code={"ImageUri": image_uri},
        PackageType="Image",
        Timeout=30,
        MemorySize=512,
        Publish=True,
    )
    logger.info(
        "Created Lambda function",
        function_name=function_name,
        image_uri=image_uri,
    )


def get_lambda_deploy_status(lambda_client: "LambdaClient", runtime_id: str):
    name = f"floww-runtime-{runtime_id}"
    try:
        res = lambda_client.get_function(FunctionName=name)
        conf = res.get("Configuration", {})

        state = conf.get("State")
        update = conf.get("LastUpdateStatus")

        if state == "Active" and update == "Successful":
            status = "COMPLETED"
        elif update == "Failed":
            status = "FAILED"
        elif state in ["Pending", "Inactive"] or update in ["InProgress"]:
            status = "IN_PROGRESS"
        else:
            status = "IN_PROGRESS"

        return {
            "success": True,
            "status": status,
            "lambda_state": state,
            "last_update_status": update,
            "logs": conf.get("LastUpdateStatusReason") or conf.get("StateReason"),
        }

    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        if code == "ResourceNotFoundException":
            return {"success": False, "status": "FAILED", "logs": "function not found"}
        return {"success": False, "status": "FAILED", "logs": msg}

    except Exception as e:
        return {"success": False, "status": "FAILED", "logs": str(e)}


def invoke_lambda_async(
    lambda_client: "LambdaClient", runtime_id: str, event_payload: dict
):
    """Invoke a Lambda function asynchronously (fire-and-forget)."""
    import json

    function_name = f"floww-runtime-{runtime_id}"

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",  # Async invocation
            Payload=json.dumps(event_payload),
        )
        logger.info(
            "Lambda invoked asynchronously",
            function_name=function_name,
            status_code=response["StatusCode"],
        )
        return {"success": True, "status_code": response["StatusCode"]}
    except ClientError as e:
        logger.error(
            "Failed to invoke Lambda",
            function_name=function_name,
            error=str(e),
        )
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(
            "Unexpected error invoking Lambda",
            function_name=function_name,
            error=str(e),
        )
        return {"success": False, "error": str(e)}
