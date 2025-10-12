from typing import Any, Dict

import boto3
import structlog
from botocore.exceptions import ClientError

logger = structlog.stdlib.get_logger(__name__)
lambda_client = boto3.client("lambda")


def deploy_lambda_function(runtime_id: str, image_uri: str) -> Dict[str, Any]:
    """Deploy a Lambda function with container image."""
    function_name = f"floww-runtime-{runtime_id}"

    try:
        # Check if function already exists
        try:
            lambda_client.get_function(FunctionName=function_name)
            function_exists = True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                function_exists = False
            else:
                raise

        if function_exists:
            # Update existing function
            response = lambda_client.update_function_code(
                FunctionName=function_name, ImageUri=image_uri
            )
            logger.info(
                "Updated Lambda function",
                function_name=function_name,
                image_uri=image_uri,
            )
        else:
            # Create new function
            response = lambda_client.create_function(
                FunctionName=function_name,
                Role="arn:aws:iam::YOUR_ACCOUNT:role/lambda-execution-role",  # TODO: Make configurable
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

        return {
            "success": True,
            "function_name": function_name,
            "state": response.get("State", "Unknown"),
            "last_update_status": response.get("LastUpdateStatus", "Unknown"),
        }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]
        logger.error(
            "Lambda deployment failed",
            function_name=function_name,
            error_code=error_code,
            error_message=error_message,
        )

        return {
            "success": False,
            "error_code": error_code,
            "error_message": error_message,
        }

    except Exception as e:
        logger.error(
            "Unexpected error deploying Lambda",
            function_name=function_name,
            error=str(e),
        )
        return {"success": False, "error_message": str(e)}


def get_lambda_deploy_status(runtime_id: str) -> Dict[str, Any]:
    """Get the deployment status of a Lambda function."""
    function_name = f"floww-runtime-{runtime_id}"

    try:
        response = lambda_client.get_function(FunctionName=function_name)

        state = response["Configuration"]["State"]
        last_update_status = response["Configuration"]["LastUpdateStatus"]

        # Map Lambda states to our RuntimeCreationStatus
        if state == "Active" and last_update_status == "Successful":
            status = "COMPLETED"
        elif state in ["Pending", "Inactive"] or last_update_status in ["InProgress"]:
            status = "IN_PROGRESS"
        elif last_update_status == "Failed":
            status = "FAILED"
        else:
            status = "IN_PROGRESS"  # Default to in progress for unknown states

        return {
            "success": True,
            "status": status,
            "lambda_state": state,
            "last_update_status": last_update_status,
            "state_reason": response["Configuration"].get("StateReason", ""),
            "last_update_status_reason": response["Configuration"].get(
                "LastUpdateStatusReason", ""
            ),
        }

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return {
                "success": False,
                "status": "FAILED",
                "error_message": "Lambda function not found",
            }
        else:
            logger.error(
                "Failed to get Lambda status",
                function_name=function_name,
                error=e.response["Error"]["Message"],
            )
            return {
                "success": False,
                "status": "FAILED",
                "error_message": e.response["Error"]["Message"],
            }

    except Exception as e:
        logger.error(
            "Unexpected error getting Lambda status",
            function_name=function_name,
            error=str(e),
        )
        return {"success": False, "status": "FAILED", "error_message": str(e)}
