"""S3 access and image URL generation (CloudFront signed URLs with S3 presigned fallback)."""

import base64
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import Settings, get_settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "image"
    return cleaned[-100:]


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket
        endpoint = settings.s3_endpoint_url or None
        public_endpoint = settings.s3_public_endpoint_url or endpoint
        common = {
            "region_name": settings.aws_region,
        }
        path_style = {"addressing_style": "path"} if endpoint else {}
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            config=Config(signature_version="s3v4", s3=path_style, retries={"max_attempts": 3}),
            **common,
        )
        accelerate = settings.s3_use_accelerate and not public_endpoint
        self._presigner = boto3.client(
            "s3",
            endpoint_url=public_endpoint,
            config=Config(
                signature_version="s3v4",
                s3={**path_style, **({"use_accelerate_endpoint": True} if accelerate else {})},
            ),
            **common,
        )
        self._cf_key = None
        if settings.cloudfront_private_key_pem:
            self._cf_key = serialization.load_pem_private_key(
                settings.cloudfront_private_key_pem.encode(), password=None
            )

    # --- uploads ---------------------------------------------------------------
    def presign_put(self, key: str, content_type: str) -> str:
        return self._presigner.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self.settings.presign_expiry_seconds,
            HttpMethod="PUT",
        )

    def head(self, key: str) -> ObjectInfo | None:
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectInfo(size=resp["ContentLength"], content_type=resp.get("ContentType", ""))

    def read(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix_chunk(self, prefix: str, limit: int = 1000) -> int:
        """Permanently delete up to `limit` object versions (and delete markers) under a prefix.

        The images bucket is versioned, so a plain delete would only add a delete marker.
        Returns how many were removed; 0 means the prefix is empty.
        """
        listing = self._client.list_object_versions(
            Bucket=self.bucket, Prefix=prefix, MaxKeys=min(limit, 1000)
        )
        targets = [
            {"Key": v["Key"], "VersionId": v["VersionId"]}
            for v in [*listing.get("Versions", []), *listing.get("DeleteMarkers", [])]
        ]
        if not targets:
            return 0
        result = self._client.delete_objects(
            Bucket=self.bucket, Delete={"Objects": targets, "Quiet": True}
        )
        errors = result.get("Errors", [])
        if errors:
            raise RuntimeError(f"S3 delete failed for {len(errors)} objects: {errors[0]}")
        return len(targets)

    # --- delivery --------------------------------------------------------------
    def image_url(self, key: str) -> str:
        s = self.settings
        if s.cloudfront_domain and self._cf_key is not None and s.cloudfront_key_pair_id:
            return self._cloudfront_signed_url(key)
        return self._presigner.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=s.image_url_expiry_seconds,
        )

    def _cloudfront_signed_url(self, key: str) -> str:
        """Canned-policy signed URL (RSA-SHA1 is mandated by the CloudFront format)."""
        s = self.settings
        url = f"https://{s.cloudfront_domain}/{quote(key)}"
        expires = int(time.time()) + s.image_url_expiry_seconds
        policy = json.dumps(
            {
                "Statement": [
                    {"Resource": url, "Condition": {"DateLessThan": {"AWS:EpochTime": expires}}}
                ]
            },
            separators=(",", ":"),
        )
        assert self._cf_key is not None
        signature = self._cf_key.sign(policy.encode(), padding.PKCS1v15(), hashes.SHA1())  # noqa: S303
        encoded = (
            base64.b64encode(signature)
            .decode()
            .replace("+", "-")
            .replace("=", "_")
            .replace("/", "~")
        )
        return f"{url}?Expires={expires}&Signature={encoded}&Key-Pair-Id={s.cloudfront_key_pair_id}"


@lru_cache
def get_storage() -> Storage:
    return Storage(get_settings())
