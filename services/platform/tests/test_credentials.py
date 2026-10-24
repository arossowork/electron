"""The agent credential port (hwplatform.credentials, #15): both adapters through one contract, the operator
allowlist (on PUT and on every resolve), the operator credential mounted from a Kubernetes Secret, the limit waits,
and the redaction of both secret formats.

Fake secrets and fake validators only: no Anthropic call.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import pytest

from hwplatform import credentials as C
from hwplatform import llm_keys
from hwplatform.logredact import redact
from hwplatform.settings import Settings

API_KEY = "sk-ant-api03-" + "FakeApiKey0123" * 6 + "AKEY"
OAT = "sk-ant-oat01-" + "FakeOAuthTok01" * 6 + "OTOK"
SECRETS = {C.API_KEY: API_KEY, C.SUBSCRIPTION_TOKEN: OAT}
TENANT = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


def svc(**kw) -> C.CredentialService:
    return C.CredentialService(Settings(**kw))


class FakeValidator:
    def __init__(self):
        self.seen: list[str] = []

    async def __call__(self, secret: str) -> None:
        self.seen.append(secret)
        if "rejected" in secret:
            raise llm_keys.KeyRejected("rejected")


# -- the adapters: one contract ---------------------------------------------------------------------------------


@pytest.mark.parametrize("ctype", C.CREDENTIAL_TYPES)
def test_adapter_contract_format_env_and_repr(ctype):
    a, secret = C.ADAPTERS[ctype], SECRETS[ctype]
    other = SECRETS[C.API_KEY if ctype == C.SUBSCRIPTION_TOKEN else C.SUBSCRIPTION_TOKEN]
    assert a.type == ctype and a.check_format(f"  {secret}\n") == secret
    for bad in (other, "sk-ant-short", "not-a-key", ""):
        with pytest.raises(llm_keys.KeyFormatError) as ei:
            a.check_format(bad)
        assert bad not in str(ei.value) or not bad
    cred = C.Credential(ctype, C.DB, secret)
    env = cred.env()
    assert set(env) == set(C.CREDENTIAL_ENV) and env[a.env_var] == secret
    assert [k for k, v in env.items() if v] == [a.env_var]  # never both credentials at once
    assert secret not in repr(cred) and cred.last4 == secret[-4:]
    assert a.limit_wait("anything", 42.0) == 42.0  # a known reset time wins


def test_limit_waits_per_adapter():
    now = 1_800_000_000.0
    api, sub = C.ADAPTERS[C.API_KEY], C.ADAPTERS[C.SUBSCRIPTION_TOKEN]
    assert api.limit_wait("rate limit", None, now) == 0.0  # the workflow's own backoff
    msg = f"Claude AI usage limit reached|{int(now) + 4 * 3600}"
    assert sub.limit_wait(msg, None, now) == 4 * 3600 + 60
    assert 0 < sub.limit_wait("5-hour limit reached ∙ resets 3pm", None, now) <= 86400 + 60
    assert sub.limit_wait("usage limit", None, now) == 900  # unknown: the local harness's 15 min


@pytest.mark.parametrize("ctype", C.CREDENTIAL_TYPES)
def test_injected_validators_are_used(ctype):
    v = FakeValidator()
    s = C.CredentialService(Settings(subscription_tenant_ids=TENANT), validators={ctype: v})
    assert s.validator(ctype) is v


def test_default_validators():
    s = svc()
    assert s.validator(C.SUBSCRIPTION_TOKEN) is C.format_only  # no documented cheap check (module doc)
    assert s.validator(C.API_KEY) is not C.format_only  # GET /v1/models


# -- the gate ---------------------------------------------------------------------------------------------------


def test_gate_off_by_default():
    s = svc()
    assert s.allowed == frozenset()
    s.check_allowed(TENANT, C.API_KEY)
    with pytest.raises(C.CredentialForbidden) as ei:
        s.check_allowed(TENANT, C.SUBSCRIPTION_TOKEN)
    assert "API key" in str(ei.value)
    with pytest.raises(llm_keys.KeyFormatError):
        s.check_allowed(TENANT, "local_login")


def test_gate_on_for_listed_tenants_only():
    s = svc(subscription_tenant_ids=f" {TENANT.upper()} , ")
    s.check_allowed(TENANT, C.SUBSCRIPTION_TOKEN)
    s.check_allowed(uuid.UUID(TENANT), C.SUBSCRIPTION_TOKEN)
    with pytest.raises(C.CredentialForbidden):
        s.check_allowed(OTHER, C.SUBSCRIPTION_TOKEN)


def test_bad_order_setting_is_refused():
    with pytest.raises(ValueError):
        svc(credential_order="operator")


# -- resolve, with the stored row faked --------------------------------------------------------------------------


@pytest.fixture
def stored(monkeypatch):
    """The tenant's stored credential row: {tenant_id: (type, secret)}."""
    rows: dict[str, tuple[str, str]] = {}

    async def fake(db, keyring, tenant_id, provider="anthropic"):
        return rows.get(str(tenant_id))

    monkeypatch.setattr(llm_keys, "get_tenant_credential", fake)
    return rows


def resolve(s, tid):
    return asyncio.run(s.resolve(tid))


def test_resolve_never_uses_a_subscription_row_of_a_tenant_not_listed(stored, caplog):
    stored[OTHER] = (C.SUBSCRIPTION_TOKEN, OAT)  # written while listed, or straight into the database
    stored[TENANT] = (C.SUBSCRIPTION_TOKEN, OAT)
    s = svc(subscription_tenant_ids=TENANT)
    with caplog.at_level(logging.DEBUG):
        assert resolve(s, OTHER) is None
    got = resolve(s, TENANT)
    assert got == C.Credential(C.SUBSCRIPTION_TOKEN, C.DB, OAT)
    assert resolve(svc(), TENANT) is None  # the gate off: no tenant resolves one
    assert OAT not in caplog.text


def test_resolve_api_key_for_everyone(stored):
    stored[OTHER] = (C.API_KEY, API_KEY)
    assert resolve(svc(), OTHER) == C.Credential(C.API_KEY, C.DB, API_KEY)
    assert resolve(svc(), str(uuid.uuid4())) is None


# -- the operator credential (k8s_secret) ---------------------------------------------------------------------


def test_operator_file_rotation_order_and_wrong_tenant(stored, tmp_path):
    f = tmp_path / "token"
    f.write_text(OAT + "\n")
    f.chmod(0o400)
    stored[TENANT] = (C.API_KEY, API_KEY)
    stored[OTHER] = (C.API_KEY, API_KEY)
    s = svc(subscription_tenant_ids=TENANT, operator_credential_file=str(f))
    got = resolve(s, TENANT)
    assert got.type == C.SUBSCRIPTION_TOKEN and got.source == C.K8S_SECRET and got.secret == OAT
    # the Secret was rotated: the next resolve reads the new file, no restart
    rotated = OAT.replace("OTOK", "ROT2")
    f.chmod(0o600)
    f.write_text(rotated)
    assert resolve(s, TENANT).secret == rotated
    # a tenant not listed never gets the operator credential, only its own API key
    assert resolve(s, OTHER) == C.Credential(C.API_KEY, C.DB, API_KEY)
    # db first for a listed tenant
    s2 = svc(subscription_tenant_ids=TENANT, operator_credential_file=str(f), credential_order="db,operator")
    assert resolve(s2, TENANT).source == C.DB
    stored.pop(TENANT)
    assert resolve(s2, TENANT).source == C.K8S_SECRET


def test_operator_file_missing_or_bad_gives_a_clear_error(stored, tmp_path):
    s = svc(subscription_tenant_ids=TENANT, operator_credential_file=str(tmp_path / "absent"))
    with pytest.raises(C.CredentialUnavailable) as ei:
        resolve(s, TENANT)
    assert "absent" in str(ei.value) and "agent.operatorCredential" in str(ei.value)
    stored[TENANT] = (C.API_KEY, API_KEY)  # the tenant's own key still works
    assert resolve(s, TENANT).source == C.DB
    bad = tmp_path / "bad"
    bad.write_text(API_KEY)  # an API key where a subscription token is configured
    s3 = svc(subscription_tenant_ids=TENANT, operator_credential_file=str(bad))
    stored.pop(TENANT)
    with pytest.raises(C.CredentialUnavailable) as ei:
        resolve(s3, TENANT)
    assert API_KEY not in str(ei.value)
    s4 = svc(
        subscription_tenant_ids=TENANT, operator_credential_file=str(bad), operator_credential_type=C.API_KEY
    )
    assert resolve(s4, TENANT) == C.Credential(C.API_KEY, C.K8S_SECRET, API_KEY)


def test_operator_credential_rejected_is_not_marked_in_the_database(monkeypatch, caplog):
    called = []

    async def fake_mark(*a, **k):
        called.append(a)

    monkeypatch.setattr(llm_keys, "mark_invalid", fake_mark)
    s = svc(subscription_tenant_ids=TENANT)
    with caplog.at_level(logging.ERROR):
        asyncio.run(s.mark_invalid(TENANT, C.Credential(C.SUBSCRIPTION_TOKEN, C.K8S_SECRET, OAT)))
    assert called == [] and "replace the Secret" in caplog.text and OAT not in caplog.text
    asyncio.run(s.mark_invalid(TENANT, C.Credential(C.API_KEY, C.DB, API_KEY)))
    assert len(called) == 1


# -- the session environment and the runner -----------------------------------------------------------------


def test_session_env_has_exactly_one_credential(tmp_path):
    from hwplatform.agent.runner import SessionSpec
    from hwplatform.agent.sandbox import child_env

    base = dict(
        ws=tmp_path, home=tmp_path, config_dir=tmp_path, bin_dir=tmp_path, tmp_dir=tmp_path,
        system="s", model="m", prompt="p",
    )  # fmt: skip
    spec = SessionSpec(**base, oauth_token=OAT)
    assert OAT not in repr(spec)
    # the worker's own environment holds an API key: it never rides along with the token
    env = child_env({"PATH": "/bin", "ANTHROPIC_API_KEY": "sk-ant-api03-worker", **spec.env()})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == OAT and "ANTHROPIC_API_KEY" not in env
    env = child_env(
        {"CLAUDE_CODE_OAUTH_TOKEN": OAT, "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "3",
         **SessionSpec(**base, api_key=API_KEY).env()}
    )  # fmt: skip
    assert env["ANTHROPIC_API_KEY"] == API_KEY
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR" not in env
    for bad in ({}, {"api_key": API_KEY, "oauth_token": OAT}):
        with pytest.raises(ValueError):
            SessionSpec(**base, **bad)


def test_runner_classifies_a_subscription_limit_message():
    from hwplatform.agent.runner import RATE_LIMITED, limit_message

    assert limit_message(f"Claude AI usage limit reached|{int(time.time()) + 600}") == RATE_LIMITED
    assert limit_message("You've hit your limit · resets 3pm") == RATE_LIMITED
    assert limit_message("Tool failed: file not found") is None and limit_message("") is None


# -- redaction ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [API_KEY, OAT])
def test_both_secret_formats_are_redacted(secret, caplog):
    from hwplatform.logredact import install_log_redaction

    out = redact(f"env CLAUDE_CODE_OAUTH_TOKEN={secret} x")
    assert secret not in out and "sk-ant-***" in out
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("hwplatform.test").info("credential %s in a line", secret)
    assert secret not in caplog.text and secret[-12:] not in caplog.text


# -- the database and the API (integration) -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("ctype", C.CREDENTIAL_TYPES)
def test_port_contract_on_the_database(make_client, settings, ctype):
    """put -> status -> resolve -> record_usage -> mark_invalid -> resolve None, for both adapters."""
    from hwplatform.crypto import Keyring
    from hwplatform.db import Database, make_engine

    from conftest import Account

    c = make_client()
    a = Account(c, f"cred-{ctype}")
    s = Settings(**{**dict(settings), "subscription_tenant_ids": a.tenant_id})
    v = FakeValidator()
    secret = SECRETS[ctype].replace("AKEY", "BKEY").replace("OTOK", "PTOK")

    async def go():
        db = Database(make_engine(settings.database_url, 1))
        try:
            svc_ = C.CredentialService(s, db, Keyring.from_settings(s), validators={ctype: v})
            tid, uid = uuid.UUID(a.tenant_id), uuid.UUID(a.user_id)
            st = await svc_.put(tid, uid, "anthropic", ctype, secret)
            assert st.credential_type == ctype and st.last4 == secret[-4:] and v.seen == [secret]
            got = await svc_.resolve(tid)
            assert got == C.Credential(ctype, C.DB, secret)
            await svc_.record_usage(tid, "claude-haiku-4-5", {"input_tokens": 3, "output_tokens": 2})
            await svc_.mark_invalid(tid, got)
            assert await svc_.resolve(tid) is None
            with pytest.raises(llm_keys.KeyRejected):
                await svc_.put(tid, uid, "anthropic", ctype, secret.replace("Fake", "rejected"))
            # the gate closes: the stored row is never used again, even of the right type
            await svc_.put(tid, uid, "anthropic", ctype, secret)
            closed = C.CredentialService(Settings(**dict(settings)), db, Keyring.from_settings(s))
            assert (await closed.resolve(tid) is None) == (ctype == C.SUBSCRIPTION_TOKEN)
        finally:
            await db.dispose()

    asyncio.run(go())


@pytest.mark.integration
def test_api_put_gate_type_and_last4_only(make_client, caplog):
    from conftest import Account

    c = make_client()
    a = Account(c, "cred-api")
    url = "/api/v1/tenant/llm-keys/anthropic"
    caplog.set_level(logging.DEBUG)
    # not listed: a subscription token is refused, nothing stored
    r = c.put(url, json={"api_key": OAT, "credential_type": "subscription_token"}, headers=a.h)
    assert r.status_code == 403 and "API key" in r.json()["detail"] and OAT not in r.text
    assert c.get(url, headers=a.h).status_code == 404
    r = c.put(url, json={"api_key": OAT, "credential_type": "local_login"}, headers=a.h)
    assert r.status_code == 422 and OAT not in r.text
    # an OAuth token sent as an API key is refused by the API key adapter's format
    r = c.put(url, json={"api_key": OAT}, headers=a.h)
    assert r.status_code == 422 and OAT not in r.text
    # listed: stored, GET shows the type and the last 4 characters only
    c.app.state.settings.subscription_tenant_ids = a.tenant_id
    r = c.put(url, json={"api_key": OAT, "credential_type": "subscription_token"}, headers=a.h)
    assert r.status_code == 200, r.text
    g = c.get(url, headers=a.h)
    assert g.json()["credential_type"] == "subscription_token" and g.json()["last4"] == OAT[-4:]
    assert set(g.json()) == {
        "provider", "last4", "status", "created_at", "rotated_at", "last_validated_at", "credential_type",
    }  # fmt: skip
    r = c.put(url, json={"api_key": API_KEY}, headers=a.h)  # one active credential: replaced by a key
    assert r.status_code == 200 and r.json()["credential_type"] == "api_key" and r.json()["rotated_at"]
    for resp in (r, g):
        assert OAT not in resp.text and API_KEY not in resp.text
    assert OAT not in caplog.text and API_KEY not in caplog.text
