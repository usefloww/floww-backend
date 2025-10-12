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


def get_image_uri(repository_name: str, tag: str) -> str | None:
    """
    Return full image URI (including sha256 digest) for a tag in ECR, or None if not found.
    Example:
      501046919403.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda@sha256:abcd...
    """
    try:
        resp = ecr_client.describe_images(
            repositoryName="trigger-lambda", imageIds=[{"imageTag": tag}]
        )
        details = resp.get("imageDetails", [])
        if not details:
            return None

        image = details[0]
        digest = image["imageDigest"]

        print("repostory_name", repository_name)

        return f"{repository_name}@{digest}"

    except ClientError as e:
        print(e)
        code = e.response["Error"]["Code"]
        if code in ("ImageNotFoundException", "RepositoryNotFoundException"):
            return None
        logger.error(
            "failed to get image uri", repo=repository_name, tag=tag, code=code
        )
        raise
    except Exception as e:
        logger.error("unexpected error", repo=repository_name, tag=tag, error=str(e))
        raise
