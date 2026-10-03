"""Object storage for screenshots (ADR 0019).

The agent uploads straight to the bucket with a pre-signed URL; the API never receives the file.
Presigning is local arithmetic (no network); only `head` and bucket creation talk to the storage.
Everything here is plain S3 (signature v4, path-style addresses), so SeaweedFS in development and
AWS S3 in production are the same code. In production the credentials come from the instance role:
no static key is ever configured (`Settings.validate_storage`).
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import boto3
import structlog
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from regista_api.core.config import Settings

log = structlog.get_logger()

_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}


@dataclass(frozen=True)
class StoredObject:
    size: int
    content_type: str


def object_key(tenant_id: object, job_id: object, artifact_id: object, content_type: str) -> str:
    """Where an artifact lives: by client and run, named by its own id. Built by the server only;
    nothing the agent says is part of it."""
    return f"tenants/{tenant_id}/jobs/{job_id}/{artifact_id}.{_EXTENSIONS[content_type]}"


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._settings = settings
        keys: dict[str, str] = {}
        if settings.s3_access_key_id and settings.s3_secret_access_key:
            keys = {
                "aws_access_key_id": settings.s3_access_key_id,
                "aws_secret_access_key": settings.s3_secret_access_key,
            }
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        common: dict[str, Any] = {"region_name": settings.s3_region, "config": config, **keys}
        # The signature covers the host, so a URL the agent or the browser will use is signed for
        # the address they reach (which may differ from the one the API itself uses).
        self._signer = boto3.client(
            "s3",
            endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url or None,
            **common,
        )
        self._client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None, **common)

    def presign_put(self, key: str, *, content_type: str, size: int, expires: int) -> str:
        """A PUT that only works for this key, this type and exactly this size."""
        url: str = self._signer.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": size,
            },
            ExpiresIn=expires,
        )
        return url

    def presign_get(self, key: str, *, content_type: str, expires: int) -> str:
        """A short-lived GET that answers with the type we recorded and as an inline image,
        whatever the object says about itself."""
        url: str = self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=expires,
        )
        return url

    async def head(self, key: str) -> StoredObject | None:
        def call() -> StoredObject | None:
            try:
                r = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise
            return StoredObject(size=int(r["ContentLength"]), content_type=str(r["ContentType"]))

        return await asyncio.to_thread(call)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def ensure_bucket(self) -> None:
        """Development and tests only: create the bucket if it is not there. Production buckets
        are made by the infrastructure, never by the application."""
        if self._settings.environment == "prod" or not self._settings.s3_endpoint_url:
            return

        def call() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        try:
            await asyncio.wait_for(asyncio.to_thread(call), timeout=10)
        except (BotoCoreError, ClientError, TimeoutError) as exc:
            # The API still serves everything else; screenshots fail until storage is up.
            log.warning("s3_bucket_not_ready", bucket=self._bucket, error=type(exc).__name__)
