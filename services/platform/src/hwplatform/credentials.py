"""The agent's Anthropic credential: one port with adapters (#15, extends the BYOK storage of #14).

Port: `CredentialService` (resolve a tenant's credential for the agent subprocess, store and validate one,
mark it invalid, record usage, turn a limit message into a wait). Behind it, one `CredentialAdapter` per
credential type and a source for the operator's mounted credential:

- `api_key` (ApiKeyAdapter): an Anthropic API key, `sk-ant-api...`, stored per tenant (envelope encryption,
  table tenant_llm_keys); given to the CLI as ANTHROPIC_API_KEY; validated with GET /v1/models; a rate limit
  waits for the API's reset time.
- `subscription_token` (SubscriptionTokenAdapter): a Claude subscription OAuth token from
  `claude setup-token`, `sk-ant-oat...`, given to the CLI as CLAUDE_CODE_OAUTH_TOKEN (never together with
  ANTHROPIC_API_KEY); a usage-limit message waits for the window reset (hwharness.limits). There is no
  documented cheap check for such a token, so the default validator checks the format only and the first
  session validates it (an auth error marks it invalid); the validator is injectable like the API key's.
- `k8s_secret` (OperatorSecretSource): the operator's credential (either type) mounted from a Kubernetes
  Secret as a read-only file; read at every resolve, so a rotated Secret applies without a restart.

Policy: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai
login or rate limits for their products, including agents built on the Claude Agent SDK" (Agent SDK
overview). So a subscription token, and the operator's mounted credential, are only for the tenants listed
in HWPLATFORM_SUBSCRIPTION_TENANT_IDS (the operator's own; empty = none). That is checked on PUT (403) and
again on every resolve, so a row that exists for a tenant no longer listed is never used.

The plaintext exists only in `Credential.secret` (repr hidden) between resolve and the subprocess env.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import llm_keys
from .crypto import CryptoError, Keyring
from .db import Database
from .settings import Settings

log = logging.getLogger("hwplatform.credentials")

API_KEY, SUBSCRIPTION_TOKEN = "api_key", "subscription_token"
CREDENTIAL_TYPES = (API_KEY, SUBSCRIPTION_TOKEN)
DB, K8S_SECRET = "db", "k8s_secret"
# Every variable a credential may set in the CLI environment: the ones a credential does not set are blanked,
# so an ANTHROPIC_API_KEY the worker happens to hold never rides along with an OAuth token (and the reverse).
CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

Validator = Callable[[str], Awaitable[None]]


class CredentialForbidden(Exception):
    """The tenant may not use this credential type (the operator allowlist)."""


class CredentialUnavailable(Exception):
    """A configured credential cannot be read (the mounted file is missing or empty)."""


@dataclass(frozen=True)
class Credential:
    type: str
    source: str  # db | k8s_secret
    secret: str = field(repr=False)

    @property
    def last4(self) -> str:
        return self.secret[-4:]

    def env(self) -> dict[str, str]:
        """The CLI environment of this credential: its variable set, the others blank (dropped by the
        launcher)."""
        var = ADAPTERS[self.type].env_var
        return {k: (self.secret if k == var else "") for k in CREDENTIAL_ENV}


class CredentialAdapter(Protocol):
    type: str
    env_var: str

    def check_format(self, raw: str) -> str: ...

    def limit_wait(self, text: str, retry_after_s: float | None, now: float | None = None) -> float: ...


_API_KEY_RE = re.compile(r"^sk-ant-(?!oat)[A-Za-z0-9_\-]{16,400}$")
_OAUTH_RE = re.compile(r"^sk-ant-oat[0-9]{2}-[A-Za-z0-9_\-]{16,400}$")


class ApiKeyAdapter:
    type = API_KEY
    env_var = "ANTHROPIC_API_KEY"

    def check_format(self, raw: str) -> str:
        key = raw.strip()
        if not _API_KEY_RE.match(key):
            raise llm_keys.KeyFormatError("not an Anthropic API key (expected sk-ant-api...)")
        return key

    def limit_wait(self, text: str, retry_after_s: float | None, now: float | None = None) -> float:
        return float(retry_after_s) if retry_after_s else 0.0  # 0: the workflow's own backoff


class SubscriptionTokenAdapter:
    type = SUBSCRIPTION_TOKEN
    env_var = "CLAUDE_CODE_OAUTH_TOKEN"

    def check_format(self, raw: str) -> str:
        tok = raw.strip()
        if not _OAUTH_RE.match(tok):
            raise llm_keys.KeyFormatError(
                "not a Claude subscription token (expected sk-ant-oat..., from `claude setup-token`)"
            )
        return tok

    def limit_wait(self, text: str, retry_after_s: float | None, now: float | None = None) -> float:
        """The subscription window's reset, from the CLI's usage-limit message (the local harness's
        parser)."""
        if retry_after_s:
            return float(retry_after_s)
        from hwharness.limits import limit_wait_seconds  # the worker side only (the `agent` extra)

        return float(limit_wait_seconds(text or "", now))


ADAPTERS: dict[str, CredentialAdapter] = {
    API_KEY: ApiKeyAdapter(),
    SUBSCRIPTION_TOKEN: SubscriptionTokenAdapter(),
}


async def format_only(secret: str) -> None:
    """The subscription token's default validator: no documented cheap check exists (see the module doc)."""
    return None


class OperatorSecretSource:
    """The operator credential mounted from a Kubernetes Secret (a file, mode 0400). Read at every resolve."""

    def __init__(self, path: str, ctype: str):
        if ctype not in CREDENTIAL_TYPES:
            raise ValueError(f"operator credential type must be one of {CREDENTIAL_TYPES}")
        self.path, self.type = Path(path), ctype

    def read(self) -> Credential:
        try:
            raw = self.path.read_text()
        except OSError as e:
            raise CredentialUnavailable(
                f"the operator credential file {self.path} cannot be read ({type(e).__name__}); "
                "check agent.operatorCredential in the Helm values"
            ) from None
        try:
            secret = ADAPTERS[self.type].check_format(raw)
        except llm_keys.KeyFormatError as e:
            raise CredentialUnavailable(f"the operator credential file {self.path}: {e}") from None
        return Credential(self.type, K8S_SECRET, secret)


class CredentialService:
    """The credential port. `validators` maps a credential type to its validator (tests inject fakes)."""

    def __init__(
        self,
        settings: Settings,
        db: Database | None = None,
        keyring: Keyring | None = None,
        *,
        validators: dict[str, Validator] | None = None,
    ):
        self.settings, self.db, self.keyring = settings, db, keyring
        self.validators = validators or {}
        self.allowed = settings.subscription_tenants()
        self.operator = (
            OperatorSecretSource(settings.operator_credential_file, settings.operator_credential_type)
            if settings.operator_credential_file
            else None
        )
        order = [o.strip() for o in settings.credential_order.split(",") if o.strip()]
        if sorted(order) != sorted([DB, "operator"]):
            raise ValueError('credential_order must be "operator,db" or "db,operator"')
        self.order = order

    # -- the gate ----------
    def is_operator_tenant(self, tenant_id: uuid.UUID | str) -> bool:
        return str(tenant_id).lower() in self.allowed

    def check_allowed(self, tenant_id: uuid.UUID | str, ctype: str) -> None:
        if ctype not in CREDENTIAL_TYPES:
            raise llm_keys.KeyFormatError(f"credential_type must be one of {', '.join(CREDENTIAL_TYPES)}")
        if ctype == SUBSCRIPTION_TOKEN and not self.is_operator_tenant(tenant_id):
            raise CredentialForbidden(
                "subscription tokens are for the operator's own tenants only "
                "(HWPLATFORM_SUBSCRIPTION_TENANT_IDS): "
                "Anthropic does not allow offering claude.ai login or subscription limits to third parties. "
                "Use an Anthropic API key."
            )

    def validator(self, ctype: str) -> Validator:
        if ctype in self.validators:
            return self.validators[ctype]
        if ctype == API_KEY:
            return llm_keys.anthropic_validator(self.settings)
        return format_only

    # -- storage (API) ----------
    async def put(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, provider: str, ctype: str, secret: str
    ) -> llm_keys.KeyStatus:
        self.check_allowed(tenant_id, ctype)
        a = ADAPTERS[ctype]
        return await llm_keys.set_key(
            self.db,
            self.keyring,
            self.validator(ctype),
            tenant_id,
            user_id,
            provider,
            secret,
            credential_type=ctype,
            fmt=a.check_format,
        )

    # -- the worker ----------
    async def _from_db(self, tenant_id: uuid.UUID, provider: str) -> Credential | None:
        try:
            got = await llm_keys.get_tenant_credential(self.db, self.keyring, tenant_id, provider)
        except CryptoError:
            log.warning("tenant %s: the stored credential does not decrypt", tenant_id)
            return None
        if got is None:
            return None
        ctype, secret = got
        if ctype not in CREDENTIAL_TYPES:
            return None
        if ctype == SUBSCRIPTION_TOKEN and not self.is_operator_tenant(tenant_id):
            log.warning(
                "tenant %s has a subscription token but is not an operator tenant: not used", tenant_id
            )
            return None
        return Credential(ctype, DB, secret)

    async def resolve(self, tenant_id: uuid.UUID | str, provider: str = "anthropic") -> Credential | None:
        """The credential an agent session of this tenant runs with, or None (no usable one).

        Operator tenants: in `credential_order` (default: the mounted operator credential, else their own).
        Every other tenant: only its own stored API key. Raises CredentialUnavailable when the operator file
        is
        configured but unreadable and the tenant has nothing else."""
        tid = uuid.UUID(str(tenant_id))
        if not self.is_operator_tenant(tid):
            return await self._from_db(tid, provider)
        err: CredentialUnavailable | None = None
        for src in self.order:
            if src == "operator" and self.operator is not None:
                try:
                    return self.operator.read()
                except CredentialUnavailable as e:
                    err = e
                    log.warning("%s", e)
            elif src == DB:
                cred = await self._from_db(tid, provider)
                if cred is not None:
                    return cred
        if err is not None:
            raise err
        return None

    async def mark_invalid(
        self, tenant_id: uuid.UUID | str, cred: Credential, provider: str = "anthropic"
    ) -> None:
        if cred.source == DB:
            await llm_keys.mark_invalid(self.db, uuid.UUID(str(tenant_id)), provider)
        else:  # the operator's Secret: nothing to flag in the database; the operator replaces the Secret
            log.error(
                "the operator credential (%s, ...%s) was rejected: replace the Secret", cred.type, cred.last4
            )

    async def record_usage(self, tenant_id: uuid.UUID | str, model: str, usage: Any, run_id=None) -> None:
        await llm_keys.record_usage(self.db, uuid.UUID(str(tenant_id)), model, usage, run_id=run_id)

    @staticmethod
    def limit_wait(
        cred: Credential, text: str, retry_after_s: float | None, now: float | None = None
    ) -> float:
        return ADAPTERS[cred.type].limit_wait(text, retry_after_s, now)
