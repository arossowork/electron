"""Platform settings, read from the environment (prefix HWPLATFORM_) or a .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HWPLATFORM_", env_file=".env", extra="ignore")

    # The API connects as a non-owner role, so row-level security applies to every query.
    database_url: str = "postgresql+asyncpg://hwflow_app:change-me-app@127.0.0.1:15432/hwflow"
    # Migrations run as the owner of the schema (role hwflow in deploy/compose).
    migration_database_url: str = "postgresql+asyncpg://hwflow:change-me-hwflow@127.0.0.1:15432/hwflow"
    # Role the migrations grant table privileges to; it must not own the tables.
    app_db_role: str = "hwflow_app"
    db_pool_size: int = 5

    jwt_secret: SecretStr = Field(default=SecretStr(""), description="HS256 key, at least 32 bytes")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "hwplatform"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600

    # BYOK envelope encryption (#14). HWPLATFORM_MASTER_KEY = one base64 32-byte key (version 1).
    # HWPLATFORM_MASTER_KEYS = "<version>:<base64>,<version>:<base64>" keyring for rotation: the highest
    # version encrypts, every listed version decrypts. Both come from a k8s Secret / .env.
    master_key: SecretStr = Field(default=SecretStr(""))
    master_keys: SecretStr = Field(default=SecretStr(""))
    anthropic_api_base: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"

    # Rate limiting and lockout (#26), stored in Postgres. Sliding windows over auth_events.
    rate_window_s: int = 60  # login and refresh
    rate_login_email_max: int = 10  # login attempts per e-mail per window
    rate_login_ip_max: int = 30  # login attempts per client IP per window
    rate_refresh_ip_max: int = 60  # refresh and verify attempts per client IP per window
    rate_signup_window_s: int = 3600
    rate_signup_email_max: int = 3
    rate_signup_ip_max: int = 10
    lockout_threshold: int = 5  # failed logins for one e-mail within lockout_window_s lock that e-mail
    lockout_ip_threshold: int = 50  # same, per client IP
    lockout_window_s: int = 15 * 60  # also the lockout duration: it ends when the failures age out

    # Sign-up e-mail verification and invitations (#26). No SMTP in M1: see mail.py.
    public_url: str = "http://127.0.0.1:8080"
    # Dev only: create the account at signup without waiting for the e-mailed token. The response is
    # identical either way, so this does not open an enumeration channel.
    dev_auto_verify: bool = False
    verify_token_ttl_s: int = 24 * 3600
    invite_ttl_s: int = 7 * 24 * 3600

    # hwplatform-purge
    purge_revoked_after_s: int = 7 * 24 * 3600  # revoked refresh tokens are kept this long (reuse detection)
    purge_events_after_s: int = 24 * 3600  # auth_events older than this (must exceed every window)

    default_max_concurrent_runs: int = 2
    # Object storage (#6): S3 API, SeaweedFS in the dev stack. Path-style addressing.
    s3_bucket: str = "hwflow-artifacts"
    s3_endpoint_url: str = "http://127.0.0.1:8333"
    s3_access_key: str = "hwflowaccess"
    s3_secret_key: SecretStr = Field(default=SecretStr("change-me-s3-secret-key"))
    s3_region: str = "us-east-1"
    s3_presign_ttl_s: int = 900

    # Event bus (#11): outbox relay -> NATS JetStream. The relay connects as the role `hwflow_relay`,
    # which sees every tenant's outbox rows (policy relay_all) and nothing else.
    nats_url: str = "nats://127.0.0.1:4222"
    relay_database_url: str = "postgresql+asyncpg://hwflow_relay:change-me-relay@127.0.0.1:15432/hwflow"
    relay_enabled: bool = True  # run the relay inside hwplatform-api (leader-elected by an advisory lock)
    relay_poll_s: float = 1.0
    relay_batch: int = 100
    outbox_purge_after_s: int = 30 * 24 * 3600  # hwplatform-purge deletes published outbox rows after this

    # Presigned download URLs (#12): the S3 endpoint a browser can reach (SeaweedFS behind the ingress).
    # Empty (dev): the API streams the object itself.
    s3_public_url: str = ""
    s3_download_url_ttl_s: int = 300

    # Temporal (#12 starts, cancels and signals workflows; the workflows are #9)
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_engine_queue: str = "hwflow-engine"

    # Uploads of design versions (ADR 6)
    max_bundle_bytes: int = 50 * 1024 * 1024  # uncompressed size of all files of one version
    max_bundle_files: int = 5000

    # SSE (ADR 5.5)
    sse_keepalive_s: float = 15.0
    sse_poll_s: float = 5.0  # outbox poll while a stream is open: fills gaps if NATS lost or delayed an event

    host: str = "127.0.0.1"
    port: int = 8080

    @field_validator("app_db_role")
    @classmethod
    def _role_is_identifier(cls, v: str) -> str:
        # Interpolated into GRANT statements in the migration, so keep it a plain identifier.
        if not v.replace("_", "").isalnum() or not v[0].isalpha():
            raise ValueError("app_db_role must be a plain SQL identifier")
        return v

    def require_jwt_secret(self) -> bytes:
        key = self.jwt_secret.get_secret_value().encode()
        if len(key) < 32:
            raise RuntimeError("HWPLATFORM_JWT_SECRET must be set to at least 32 bytes")
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
