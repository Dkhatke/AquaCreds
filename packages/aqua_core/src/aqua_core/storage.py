"""S3-compatible object storage (SeaweedFS in development, Cloudflare R2 in production)."""

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from aqua_core.settings import Settings

_NOT_FOUND = {"404", "NoSuchBucket", "NotFound", "NoSuchKey"}


def s3_client(settings: Settings, *, public: bool = False) -> Any:
    endpoint = (settings.s3_public_endpoint_url if public else None) or settings.s3_endpoint_url
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
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
        if exc.response.get("Error", {}).get("Code") not in _NOT_FOUND:
            raise
    client.create_bucket(Bucket=settings.s3_bucket)
    return True


def check_storage(settings: Settings) -> None:
    """Raise if the configured bucket is not reachable."""
    s3_client(settings).head_bucket(Bucket=settings.s3_bucket)


@dataclass(frozen=True)
class StoredObject:
    size_bytes: int
    content_type: str
    sha256: str


class ObjectStorage(Protocol):
    def presign_put(self, key: str, content_type: str) -> str: ...

    def describe(self, key: str) -> StoredObject | None:
        """Size, content type and SHA-256 of an object, or None if it does not exist."""
        ...

    def get_bytes(self, key: str, max_bytes: int) -> bytes:
        """Object contents (raises ValueError if larger than max_bytes)."""
        ...

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Upload `data` (server-side workers only). Returns its SHA-256."""
        ...


class S3ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = s3_client(settings)
        self._public = s3_client(settings, public=True)

    def presign_put(self, key: str, content_type: str) -> str:
        url: str = self._public.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._settings.s3_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self._settings.upload_url_ttl_s,
        )
        return url

    def describe(self, key: str) -> StoredObject | None:
        try:
            obj = self._client.get_object(Bucket=self._settings.s3_bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND:
                return None
            raise
        digest = hashlib.sha256()
        size = 0
        for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        return StoredObject(
            size_bytes=size,
            content_type=str(obj.get("ContentType", "application/octet-stream")),
            sha256=digest.hexdigest(),
        )

    def get_bytes(self, key: str, max_bytes: int) -> bytes:
        obj = self._client.get_object(Bucket=self._settings.s3_bucket, Key=key)
        if int(obj.get("ContentLength", 0)) > max_bytes:
            raise ValueError(f"{key} exceeds {max_bytes} bytes")
        data: bytes = obj["Body"].read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"{key} exceeds {max_bytes} bytes")
        return data

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self._client.put_object(
            Bucket=self._settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
        return digest
