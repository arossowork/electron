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

    default_max_concurrent_runs: int = 2
    s3_bucket: str = "hwflow-artifacts"

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
