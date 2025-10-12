import boto3
import structlog
from botocore.exceptions import ClientError

from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)
lambda_client = boto3.client(
    "lambda",
    region_name=settings.AWS_REGION,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
)


def deploy_lambda_function(runtime_id: str, image_uri: str):
    """Deploy a Lambda function with container image."""
    function_name = f"floww-runtime-{runtime_id}"

    response = lambda_client.create_function(
        FunctionName=function_name,
        Role=settings.LAMBDA_EXECUTION_ROLE_ARN,
        Code={"ImageUri": image_uri},
        PackageType="Image",
        Timeout=30,
        MemorySize=512,
        Publish=True,
    )
    print(response)
    logger.info(
        "Created Lambda function",
        function_name=function_name,
        image_uri=image_uri,
    )


def get_lambda_deploy_status(runtime_id: str):
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
