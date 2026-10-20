import math
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- environment --------------------------------------------------------
    # Defaults to "production" so a forgotten variable fails closed: dev auth, plaintext DB
    # connections and missing Cognito settings are all rejected outside `local`.
    environment: Literal["local", "staging", "production"] = "production"

    # --- database -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://labeler:labeler@localhost:5432/labeler"

    # Connection budget. All API pods together may hold at most `db_connection_budget`
    # connections, so each pod gets budget / max_api_pods (see README, "Database connections").
    # Explicit DB_POOL_SIZE / DB_MAX_OVERFLOW override the derived values (tests, local).
    db_connection_budget: int = 340
    max_api_pods: int = 20
    db_pool_size: int | None = None
    db_max_overflow: int | None = None
    # Seconds to wait for a free connection before answering 503 instead of hanging.
    db_pool_timeout: float = 10.0
    # disable: plaintext (local only). require: TLS without verification.
    # verify-full: TLS, CA and hostname verified (required outside local).
    db_ssl_mode: Literal["disable", "require", "verify-full"] = "disable"
    db_ssl_ca_file: str = ""

    # --- auth ---------------------------------------------------------------
    # "cognito": validate RS256 tokens against a JWKS (Cognito, or static JWKS JSON).
    # "dev": additionally accept HS256 tokens minted by POST /dev/login (local only).
    auth_mode: Literal["cognito", "dev"] = "cognito"
    # Clock slack when comparing a token's issue time to a role change recorded by the database.
    token_cutoff_skew_seconds: int = 2
    cognito_region: str = "us-east-1"
    # Override for tests / emulators; empty = the real Cognito endpoint.
    cognito_endpoint_url: str = ""
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
    # Decoded-size limits. Compressed size alone does not bound memory use: a tiny PNG can
    # declare billions of pixels.
    max_image_pixels: int = 100_000_000
    max_image_side: int = 20_000

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

    # --- rate limiting (per user, per pod; the WAF is the IP-level control) --
    rate_limit_enabled: bool = True
    rate_limit_claim_per_minute: int = 60
    rate_limit_presign_per_minute: int = 60
    rate_limit_export_per_minute: int = 6

    # --- admin / export -----------------------------------------------------
    export_page_size: int = 200
    # Exports hold a connection for as long as the client keeps downloading, so they get their own
    # tiny pool (carved out of the per-pod budget) and can never starve labelers and reviewers.
    export_pool_size: int = 2

    # Deleting an archived batch removes its S3 objects and rows in chunks. The cooldown after
    # archiving guarantees no presigned upload URL is still valid when objects are listed
    # (None = presign_expiry_seconds).
    batch_delete_cooldown_seconds: int | None = None
    batch_delete_s3_chunk: int = 1000
    batch_delete_row_chunk: int = 2000
    batch_delete_time_budget_seconds: float = 20.0

    # --- observability ------------------------------------------------------
    log_json: bool = True
    log_level: str = "INFO"
    # "cloudwatch": the reaper also calls PutMetricData directly (needs IRSA permission), instead of
    # relying on a log pipeline to turn the EMF lines into metrics.
    metrics_sink: Literal["log", "cloudwatch"] = "log"
    cloudwatch_endpoint_url: str = ""
    metrics_enabled: bool = True

    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def _per_pod_budget(self) -> int:
        """Connections one pod may hold in total: the main pool plus the export pool."""
        share = self.db_connection_budget // max(self.max_api_pods, 1)
        return max(share, self.export_pool_size + 2)

    @property
    def effective_pool_size(self) -> int:
        if self.db_pool_size is not None:
            return self.db_pool_size
        main = self._per_pod_budget - self.export_pool_size
        return max(math.ceil(main * 0.6), 1)

    @property
    def effective_max_overflow(self) -> int:
        if self.db_max_overflow is not None:
            return self.db_max_overflow
        main = self._per_pod_budget - self.export_pool_size
        return max(main - self.effective_pool_size, 0)

    @model_validator(mode="after")
    def _enforce_environment_safety(self) -> "Settings":
        if self.environment == "local":
            return self
        problems: list[str] = []
        if self.auth_mode == "dev":
            problems.append("AUTH_MODE=dev is only allowed when ENVIRONMENT=local")
        if self.dev_auth_secret:
            problems.append("DEV_AUTH_SECRET must not be set outside ENVIRONMENT=local")
        if not self.cognito_user_pool_id or not self.cognito_client_id:
            problems.append("COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are required")
        if self.jwks_json:
            problems.append("JWKS_JSON (static keys) is only allowed when ENVIRONMENT=local")
        if self.db_ssl_mode != "verify-full":
            problems.append("DB_SSL_MODE must be verify-full")
        if problems:
            raise ValueError(
                f"Unsafe configuration for ENVIRONMENT={self.environment}: " + "; ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
