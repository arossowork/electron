"""Fixtures. Integration tests need a Postgres prepared like deploy/compose/postgres-init.sh does:
an owner role (runs the migrations) and a non-owner app role (the API's connection).

  HWPLATFORM_TEST_OWNER_URL  default postgresql://hwflow:change-me-hwflow@127.0.0.1:15432/hwflow
  HWPLATFORM_TEST_APP_URL    default postgresql://hwflow_app:change-me-app@127.0.0.1:15432/hwflow

If the database does not answer, every test marked `integration` is skipped. The schema is reset
(downgrade base, upgrade head) once per session, so do not point these at a database you care about.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import uuid

import asyncpg
import pytest

OWNER_URL = os.environ.get(
    "HWPLATFORM_TEST_OWNER_URL", "postgresql://hwflow:change-me-hwflow@127.0.0.1:15432/hwflow"
)
APP_URL = os.environ.get(
    "HWPLATFORM_TEST_APP_URL", "postgresql://hwflow_app:change-me-app@127.0.0.1:15432/hwflow"
)
# The real Claude API smoke (#15) is collected only on request (`-m anthropic_smoke`, or HWPLATFORM_AGENT_SMOKE=1)
# and with a credential of its own in the environment (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN): never in CI
# (which fails on any skipped test) and never with the developer's interactive login. See test_agent_smoke.py.
SMOKE_CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def pytest_ignore_collect(collection_path, config):
    if collection_path.name not in ("test_agent_smoke.py", "test_eval_smoke.py"):
        return None
    expr = config.getoption("markexpr", "") or ""
    asked = bool(re.search(r"(?<!not )\banthropic_smoke\b", expr)) or (
        os.environ.get("HWPLATFORM_AGENT_SMOKE") == "1"
    )
    has_credential = any(os.environ.get(v) for v in SMOKE_CREDENTIAL_VARS)
    return None if asked and has_credential else True


JWT_SECRET = "test-secret-" + "x" * 40
LOOSE_LIMITS = dict(
    rate_login_email_max=10_000,
    rate_login_ip_max=10_000,
    rate_refresh_ip_max=10_000,
    rate_signup_email_max=10_000,
    rate_signup_ip_max=10_000,
    lockout_threshold=10_000,
    lockout_ip_threshold=10_000,
)
MASTER_KEY = base64.b64encode(b"m" * 32).decode()


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
        master_key=MASTER_KEY,
        db_pool_size=2,
        relay_enabled=False,  # the relay has its own tests; the API suite must not start one per app
        dev_auto_verify=True,  # most tests only need an account; the verification flow has its own tests
        **LOOSE_LIMITS,  # all test clients share one IP, so the general suite must not trip the limiter
    )


class FakeValidator:
    """Stands in for the Anthropic API: keys containing 'rejected' are refused, 'down' = unreachable."""

    def __init__(self):
        self.seen: list[str] = []

    async def __call__(self, api_key: str) -> None:
        from hwplatform.llm_keys import KeyRejected, ValidatorUnavailable

        self.seen.append(api_key)
        if "rejected" in api_key:
            raise KeyRejected("the Anthropic API rejected this key")
        if "down" in api_key:
            raise ValidatorUnavailable("cannot reach the Anthropic API")


@pytest.fixture(scope="session")
def validator():
    return FakeValidator()


@pytest.fixture(scope="session")
def mailbox():
    from hwplatform.mail import InMemoryEmailSender

    return InMemoryEmailSender()


@pytest.fixture(scope="session")
def client(settings, validator, mailbox):
    from fastapi.testclient import TestClient

    from hwplatform.api.app import create_app

    with TestClient(create_app(settings, key_validator=validator, email_sender=mailbox)) as c:
        yield c


def random_ip() -> str:
    n = uuid.uuid4().int
    return f"10.{n % 250}.{(n >> 8) % 250}.{(n >> 16) % 250}"


@pytest.fixture
def make_client(settings, validator):
    """A separate app with changed settings (tight limits, no auto-verify) and its own client IP."""
    from fastapi.testclient import TestClient

    from hwplatform.api.app import create_app
    from hwplatform.mail import InMemoryEmailSender
    from hwplatform.settings import Settings

    opened = []

    def make(ip: str | None = None, **overrides):
        s = Settings(**{**dict(settings), **overrides})
        box = InMemoryEmailSender()
        c = TestClient(
            create_app(s, key_validator=validator, email_sender=box), client=(ip or random_ip(), 5000)
        )
        c.__enter__()
        opened.append(c)
        c.mailbox = box
        return c

    yield make
    for c in opened:
        c.__exit__(None, None, None)


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
        assert r.status_code == 202, r.text  # dev_auto_verify: the account exists now
        r = client.post("/api/v1/auth/login", json={"email": self.email, "password": password})
        assert r.status_code == 200, r.text
        self.tokens = r.json()
        self.tenant_id = self.tokens["tenant_id"]
        self.user_id = self.tokens["user_id"]

    @property
    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens['access_token']}"}

    def join(self, owner: Account, role: str = "member") -> dict:
        """`owner` invites this account by e-mail, this account accepts; returns a login in owner's tenant."""
        c = self.client
        r = c.post("/api/v1/tenant/members", json={"email": self.email, "role": role}, headers=owner.h)
        assert r.status_code == 202, r.text
        token = c.app.state.mailer.last(self.email, "invite").token
        r = c.post("/api/v1/auth/accept-invite", json={"token": token}, headers=self.h)
        assert r.status_code == 200, r.text
        r = c.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password, "tenant_id": owner.tenant_id},
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def project(self, name: str) -> dict:
        r = self.client.post("/api/v1/projects", json={"name": name}, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
def account(client):
    return lambda tag: Account(client, tag)


# --- object storage, NATS, run helpers (platform runs, #6 #7 #11) ---------------------------------------
# Defaults match deploy/compose/.env.example; override with HWPLATFORM_TEST_* when the stack uses other ports.

S3_ENDPOINT = os.environ.get("HWPLATFORM_TEST_S3_ENDPOINT", "http://127.0.0.1:8333")
S3_ACCESS = os.environ.get("HWPLATFORM_TEST_S3_ACCESS_KEY", "hwflowaccess")
S3_SECRET = os.environ.get("HWPLATFORM_TEST_S3_SECRET_KEY", "change-me-s3-secret-key")
S3_BUCKET = os.environ.get("HWPLATFORM_TEST_S3_BUCKET", "hwflow-artifacts")
NATS_URL = os.environ.get("HWPLATFORM_TEST_NATS_URL", "nats://127.0.0.1:4222")
RELAY_URL = os.environ.get(
    "HWPLATFORM_TEST_RELAY_URL", "postgresql://hwflow_relay:change-me-relay@127.0.0.1:15432/hwflow"
)


@pytest.fixture(scope="session")
def s3_settings():
    from pydantic import SecretStr

    from hwplatform.settings import Settings

    return Settings(
        s3_endpoint_url=S3_ENDPOINT,
        s3_access_key=S3_ACCESS,
        s3_secret_key=SecretStr(S3_SECRET),
        s3_bucket=S3_BUCKET,
    )


@pytest.fixture(scope="session")
def s3_client(s3_settings):
    """A boto3 client on the compose SeaweedFS; creates the bucket if the compose job has not."""
    import time

    from botocore.exceptions import BotoCoreError, ClientError

    from hwplatform.storage import make_s3_client

    c = make_s3_client(s3_settings)
    err: Exception | None = None
    for _ in range(20):  # a freshly started SeaweedFS answers a few seconds before its filer is ready
        try:
            try:
                c.head_bucket(Bucket=S3_BUCKET)
            except ClientError as e:
                if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
                c.create_bucket(Bucket=S3_BUCKET)
            break
        except (BotoCoreError, ClientError) as e:
            err = e
            time.sleep(1)
    else:
        pytest.skip(f"no S3 (SeaweedFS) at {S3_ENDPOINT} ({type(err).__name__}); see docs/platform-dev.md")
    return c


@pytest.fixture
def s3_store(s3_client):
    """A store factory: each call is a fresh tenant (random UUID), so tests never see each other's objects."""
    from hwplatform.storage import S3ArtifactStore

    def make(tenant_id=None, **kw):
        return S3ArtifactStore(s3_client, S3_BUCKET, tenant_id or uuid.uuid4(), **kw)

    return make


@pytest.fixture(scope="session")
def nats_ready():
    import nats

    async def ping():
        nc = await nats.connect(NATS_URL, connect_timeout=3, max_reconnect_attempts=0)
        await nc.close()

    try:
        asyncio.run(ping())
    except Exception as e:
        pytest.skip(f"no NATS reachable at {NATS_URL} ({type(e).__name__}); see docs/platform-dev.md")
    return NATS_URL


@pytest.fixture(scope="session")
def relay_ready(db_ready, nats_ready):
    why = asyncio.run(_reachable(RELAY_URL))
    if why:
        pytest.skip(f"relay role cannot connect ({why}); is hwflow_relay created by postgres-init.sh?")
    return RELAY_URL


class RunEnv:
    """A tenant with one project and the store bound to it, for run-store tests."""

    def __init__(self, db, store, tenant_id, project_id):
        self.db, self.store, self.tenant_id, self.project_id = db, store, tenant_id, project_id

    async def design_version(self, tag: str = "v"):
        from hwflow.artifacts import ArtifactRef

        def sha(s: str) -> str:
            return (s.encode().hex() * 64)[:64]

        return await self.store.create_design_version(
            self.project_id,
            content_sha256=sha(tag + uuid.uuid4().hex),
            design=ArtifactRef(sha("d" + tag), 10, "application/yaml"),
            project=ArtifactRef(sha("p" + tag), 20, "application/vnd.hwflow.tree+json"),
        )

    async def new_run(self, dv=None, **kw):
        from datetime import UTC, datetime

        from hwflow.runs import Run, RunStatus

        dv = dv or await self.design_version()
        run = Run(
            run_id="",
            kind="build",
            tenant_id=str(self.tenant_id),
            project_id=str(self.project_id),
            design_version=dv,
            status=RunStatus.queued,
            verdict=None,
            options={"until": "sim"},
            options_hash="abc123abc123",
            settings={"a": 1},
            code={"hwflow": "0.0"},
            tools={},
            stages=[],
            artifacts={},
            created_at=datetime.now(UTC),
        )
        return await self.store.create(run, **kw)

    async def events(self, run_id=None):
        """Outbox rows of this tenant (RLS applies), oldest first."""
        from sqlalchemy import text

        async with self.db.tx(tenant_id=self.tenant_id) as s:
            q = "SELECT type, seq, run_id, subject, envelope, event_id, published_at FROM outbox WHERE tenant_id = :t"
            args = {"t": self.tenant_id}
            if run_id:
                q += " AND run_id = :r"
                args["r"] = uuid.UUID(str(run_id))
            return (await s.execute(text(q + " ORDER BY id"), args)).mappings().all()


@pytest.fixture
def run_env(settings, account):
    """Factory for an async context: `async with run_env() as env:` (own Database per event loop)."""
    import contextlib

    from hwplatform.db import Database
    from hwplatform.runs import PgRunStore

    @contextlib.asynccontextmanager
    async def make(tag: str = "runs"):
        acct = account(tag)
        project = acct.project("proj")
        db = Database.from_settings(settings)
        try:
            yield RunEnv(
                db, PgRunStore(db, acct.tenant_id), uuid.UUID(acct.tenant_id), uuid.UUID(project["id"])
            )
        finally:
            await db.dispose()

    return make


# --- API with a fake workflow gateway (#12) --------------------------------------------------------------


class FakeGateway:
    """Records what the API asked Temporal to do; `fail` / `missing` simulate an unreachable or empty engine."""

    def __init__(self):
        self.started: list[tuple[str, object]] = []
        self.cancelled: list[str] = []
        self.signals: list[tuple[str, object]] = []
        self.fail = False
        self.missing: set[str] = set()

    def _check(self):
        from hwplatform.gateway import GatewayError

        if self.fail:
            raise GatewayError("fake: engine down")

    async def start_build(self, workflow_id, arg):
        self._check()
        self.started.append((workflow_id, arg))

    async def start_design(self, workflow_id, arg):
        self._check()
        self.started.append((workflow_id, arg))

    async def start_eval(self, workflow_id, arg):
        self._check()
        self.started.append((workflow_id, arg))

    async def cancel(self, workflow_id):
        from hwplatform.gateway import WorkflowNotFound

        self._check()
        if workflow_id in self.missing:
            raise WorkflowNotFound(workflow_id)
        self.cancelled.append(workflow_id)

    async def signal_answer(self, workflow_id, answer):
        self._check()
        self.signals.append((workflow_id, answer))

    async def signal_retry(self, workflow_id):
        self._check()
        self.signals.append((workflow_id, "retry"))

    async def close(self):
        return None


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def api_settings(settings, s3_settings):
    """Settings for an API app: the Postgres of `settings`, the S3 of the test stack, no relay."""

    def make(**overrides):
        from hwplatform.settings import Settings

        return Settings(
            **{
                **dict(settings),
                "s3_endpoint_url": s3_settings.s3_endpoint_url,
                "s3_access_key": s3_settings.s3_access_key,
                "s3_secret_key": s3_settings.s3_secret_key,
                "s3_bucket": s3_settings.s3_bucket,
                "nats_url": NATS_URL,
                "relay_database_url": sa_url(RELAY_URL),
                **overrides,
            }
        )

    return make


@pytest.fixture
def make_api(api_settings, validator, gateway, s3_client, nats_ready):
    """A TestClient on an app wired to the fake gateway; keyword overrides go to Settings."""
    from fastapi.testclient import TestClient

    from hwplatform.api.app import create_app
    from hwplatform.mail import InMemoryEmailSender

    opened = []

    def make(**overrides):
        c = TestClient(
            create_app(
                api_settings(**overrides),
                key_validator=validator,
                email_sender=InMemoryEmailSender(),
                gateway=gateway,
            ),
            client=(random_ip(), 5000),
        )
        c.__enter__()
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def api(make_api):
    return make_api()
