"""Fixtures. Integration tests need a Postgres prepared like deploy/compose/postgres-init.sh does:
an owner role (runs the migrations) and a non-owner app role (the API's connection).

  HWPLATFORM_TEST_OWNER_URL  default postgresql://hwflow:change-me-hwflow@127.0.0.1:15432/hwflow
  HWPLATFORM_TEST_APP_URL    default postgresql://hwflow_app:change-me-app@127.0.0.1:15432/hwflow

If the database does not answer, every test marked `integration` is skipped. The schema is reset
(downgrade base, upgrade head) once per session, so do not point these at a database you care about.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

OWNER_URL = os.environ.get(
    "HWPLATFORM_TEST_OWNER_URL", "postgresql://hwflow:change-me-hwflow@127.0.0.1:15432/hwflow"
)
APP_URL = os.environ.get(
    "HWPLATFORM_TEST_APP_URL", "postgresql://hwflow_app:change-me-app@127.0.0.1:15432/hwflow"
)
JWT_SECRET = "test-secret-" + "x" * 40


def sa_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _reachable(url: str) -> str | None:
    try:
        conn = await asyncpg.connect(url, timeout=3)
    except Exception as e:  # any failure means "no DB here"
        return f"{type(e).__name__}: {e}"
    await conn.close()
    return None


@pytest.fixture(scope="session")
def db_ready():
    why = asyncio.run(_reachable(OWNER_URL)) or asyncio.run(_reachable(APP_URL))
    if why:
        pytest.skip(f"no test Postgres reachable ({why}); see services/platform/README.md")
    from alembic import command

    from hwplatform.main import alembic_config

    cfg = alembic_config(sa_url(OWNER_URL), app_role="hwflow_app")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return True


@pytest.fixture(scope="session")
def settings(db_ready):
    from hwplatform.settings import Settings

    return Settings(
        database_url=sa_url(APP_URL),
        migration_database_url=sa_url(OWNER_URL),
        jwt_secret=JWT_SECRET,
        db_pool_size=2,
    )


@pytest.fixture(scope="session")
def client(settings):
    from fastapi.testclient import TestClient

    from hwplatform.api.app import create_app

    with TestClient(create_app(settings)) as c:
        yield c


def unique_email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@example.com"


class Account:
    """A signed-up user with its own tenant."""

    def __init__(self, client, tag: str, password: str = "a-long-password-123"):
        self.client = client
        self.email = unique_email(tag)
        self.password = password
        r = client.post(
            "/api/v1/auth/signup",
            json={"email": self.email, "password": password, "tenant_name": f"Tenant {tag}"},
        )
        assert r.status_code == 201, r.text
        self.tokens = r.json()
        self.tenant_id = self.tokens["tenant_id"]
        self.user_id = self.tokens["user_id"]

    @property
    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens['access_token']}"}

    def project(self, name: str) -> dict:
        r = self.client.post("/api/v1/projects", json={"name": name}, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
def account(client):
    return lambda tag: Account(client, tag)
