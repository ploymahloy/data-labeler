from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://labeler:labeler@localhost:5432/labeler"
    db_pool_size: int = 20
    db_max_overflow: int = 20

    # --- auth ---------------------------------------------------------------
    # "cognito": validate RS256 tokens against a JWKS (Cognito, or static JWKS JSON).
    # "dev": additionally accept HS256 tokens minted by POST /dev/login (local only).
    auth_mode: Literal["cognito", "dev"] = "cognito"
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    # Optional static JWKS (used by tests and offline development) instead of fetching.
    jwks_json: str = ""
    dev_auth_secret: str = ""

    # --- storage ------------------------------------------------------------
    s3_bucket: str = "labeler-images"
    aws_region: str = Field(default="us-east-1", validation_alias="AWS_REGION")
    # Endpoint the API uses for server-side calls (e.g. LocalStack inside docker).
    s3_endpoint_url: str = ""
    # Endpoint embedded in presigned URLs handed to browsers. Defaults to s3_endpoint_url.
    s3_public_endpoint_url: str = ""
    s3_use_accelerate: bool = False
    presign_expiry_seconds: int = 900
    max_upload_bytes: int = 25 * 1024 * 1024
    max_files_per_request: int = 200

    # CloudFront signed URLs for image delivery (falls back to S3 presigned GET).
    cloudfront_domain: str = ""
    cloudfront_key_pair_id: str = ""
    cloudfront_private_key_pem: str = ""
    image_url_expiry_seconds: int = 3600

    # --- workflow -----------------------------------------------------------
    lease_minutes: int = 15
    max_rejections: int = 3
    rework_reserve_hours: int = 24
    duplicate_iou_threshold: float = 0.9
    max_annotations_per_image: int = 500

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
