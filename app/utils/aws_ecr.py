import boto3
import structlog
from botocore.exceptions import ClientError

from app.settings import settings

logger = structlog.stdlib.get_logger(__name__)

# Initialize ECR client
ecr_client = boto3.client(
    "ecr",
    region_name=settings.AWS_REGION,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
)


def check_ecr_image_exists(repository_name: str, tag: str) -> bool:
    """
    Check if an image with the specified tag exists in the ECR repository.

    Args:
        repository_name: The name of the ECR repository
        tag: The image tag to check

    Returns:
        True if the image exists, False otherwise
    """
    try:
        response = ecr_client.describe_images(
            repositoryName=repository_name, imageIds=[{"imageTag": tag}]
        )
        # If we get a response with imageDetails, the image exists
        return len(response.get("imageDetails", [])) > 0
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ImageNotFoundException":
            # Image doesn't exist
            return False
        elif error_code == "RepositoryNotFoundException":
            # Repository doesn't exist, so image doesn't exist
            return False
        else:
            # Other errors should be logged and raised
            logger.error(
                "Failed to check ECR image existence",
                repository=repository_name,
                tag=tag,
                error_code=error_code,
                error_message=e.response["Error"]["Message"],
            )
            raise
    except Exception as e:
        logger.error(
            "Unexpected error checking ECR image existence",
            repository=repository_name,
            tag=tag,
            error=str(e),
        )
        raise
