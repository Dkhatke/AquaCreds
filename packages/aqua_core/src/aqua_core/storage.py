"""S3-compatible object storage (SeaweedFS in development, Cloudflare R2 in production)."""

from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from aqua_core.settings import Settings


def s3_client(settings: Settings) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=3,
            read_timeout=10,
        ),
    )


def ensure_bucket(settings: Settings) -> bool:
    """Create the configured bucket if missing. Returns True if it was created."""
    client = s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        return False
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    client.create_bucket(Bucket=settings.s3_bucket)
    return True


def check_storage(settings: Settings) -> None:
    """Raise if the configured bucket is not reachable."""
    s3_client(settings).head_bucket(Bucket=settings.s3_bucket)
