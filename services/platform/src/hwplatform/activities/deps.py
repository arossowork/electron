"""What the activities of one worker process share: settings, the database, the S3 client, the worker dirs,
the
Temporal client (agent tools start linked workflows), the BYOK keyring and the agent runner."""

from __future__ import annotations

import tempfile
from functools import cached_property
from pathlib import Path

from ..db import Database
from ..settings import Settings
from ..storage import S3ArtifactStore, make_s3_client


class Deps:
    def __init__(
        self,
        settings: Settings,
        db: Database | None = None,
        s3_client=None,
        *,
        temporal=None,
        runner=None,
        keyring=None,
    ):
        self.settings = settings
        self._db = db
        self._s3 = s3_client
        self.temporal = temporal  # set by the worker once it is connected
        self._runner = runner
        self._keyring = keyring

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = Database.from_settings(self.settings)
        return self._db

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = make_s3_client(self.settings)
        return self._s3

    @property
    def keyring(self):
        if self._keyring is None:
            from ..crypto import Keyring

            self._keyring = Keyring.from_settings(self.settings)
        return self._keyring

    @property
    def runner(self):
        """The agent session runner (hwplatform.agent.runner.SdkRunner unless a test injected one)."""
        if self._runner is None:
            from ..agent.runner import SdkRunner

            self._runner = SdkRunner()
        return self._runner

    def store(self, tenant_id: str) -> S3ArtifactStore:
        return S3ArtifactStore(self.s3, self.settings.s3_bucket, tenant_id)

    @cached_property
    def root(self) -> Path:
        p = Path(self.settings.worker_dir or Path(tempfile.gettempdir()) / "hwflow-worker").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def scratch(self) -> Path:
        p = self.root / "scratch"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def tool_cache(self) -> Path:
        p = self.root / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def close(self) -> None:
        if self._db is not None:
            await self._db.dispose()
