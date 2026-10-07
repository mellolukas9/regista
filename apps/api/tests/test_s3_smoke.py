"""Acceptance test of ADR 0019: the dev S3 (SeaweedFS) must honour what the artifact flow relies on.

If any of these fails, the ADR picks another option before M3 goes on.
"""

import time
from typing import Any

import boto3
import httpx
import pytest
from botocore.config import Config

from .conftest import S3Env

BUCKET = "smoke"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)


@pytest.fixture(scope="module")
def s3(s3_env: S3Env) -> Any:
    client = boto3.client(
        "s3",
        endpoint_url=s3_env.endpoint_url,
        aws_access_key_id=s3_env.access_key_id,
        aws_secret_access_key=s3_env.secret_access_key,
        region_name=s3_env.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=BUCKET)
    return client


def _put_url(s3: Any, key: str, expires: int = 120) -> str:
    url: str = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET,
            "Key": key,
            "ContentType": "image/png",
            "ContentLength": len(PNG),
        },
        ExpiresIn=expires,
    )
    return url


def test_put_with_the_signed_type_and_size_works(s3: Any) -> None:
    url = _put_url(s3, "ok.png")
    r = httpx.put(url, content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    head = s3.head_object(Bucket=BUCKET, Key="ok.png")
    assert head["ContentLength"] == len(PNG)
    assert head["ContentType"] == "image/png"


def test_put_with_another_size_or_type_is_refused(s3: Any) -> None:
    url = _put_url(s3, "bad.png")
    bigger = httpx.put(url, content=PNG + b"x", headers={"Content-Type": "image/png"})
    assert bigger.status_code >= 400, "a body of another size was accepted"
    other_type = httpx.put(url, content=PNG, headers={"Content-Type": "text/html"})
    assert other_type.status_code >= 400, "another Content-Type was accepted"
    with pytest.raises(Exception):  # noqa: B017  (the object must not exist)
        s3.head_object(Bucket=BUCKET, Key="bad.png")


def test_an_expired_put_url_is_refused(s3: Any) -> None:
    url = _put_url(s3, "late.png", expires=1)
    time.sleep(3)
    r = httpx.put(url, content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code >= 400


def test_get_forces_the_signed_content_type_and_disposition(s3: Any) -> None:
    # Stored as something else on purpose: the signed response overrides win.
    s3.put_object(Bucket=BUCKET, Key="page.bin", Body=b"<html>x</html>", ContentType="text/html")
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": BUCKET,
            "Key": "page.bin",
            "ResponseContentType": "image/png",
            "ResponseContentDisposition": "inline",
        },
        ExpiresIn=60,
    )
    r = httpx.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == "inline"


def test_an_expired_get_url_is_refused(s3: Any) -> None:
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": "ok.png"}, ExpiresIn=1
    )
    time.sleep(3)
    assert httpx.get(url).status_code >= 400


def test_the_bucket_is_private(s3_env: S3Env, s3: Any) -> None:
    assert httpx.get(f"{s3_env.endpoint_url}/{BUCKET}/ok.png").status_code >= 400
