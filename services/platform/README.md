# hwplatform: the hosted multi-tenant service

The platform side of epic #21. It is a uv workspace member, separate from the engine: `src/hwflow` knows nothing about tenants, users or HTTP. This package holds the tenancy data model and the built-in auth (#13); the API for designs, runs and artifacts (#12) and BYOK keys (#14) build on it.

## Architecture

```
src/hwplatform/
  settings.py      pydantic-settings, env prefix HWPLATFORM_
  db.py            async engine; Database.tx(tenant_id, user_id, lookup_email) = one transaction with the RLS context
  models.py        tenants, users, memberships, projects, refresh_tokens (SQLAlchemy 2)
  crypto.py        envelope encryption (AES-256-GCM, DEK wrapped by a versioned master key keyring)
  llm_keys.py      BYOK service: store/validate/read a tenant's LLM key; get_tenant_llm_key(); record_usage()
  rotation.py      hwplatform-rotate-master: re-wrap all DEKs under the newest master key
  logredact.py     log record factory that redacts sk-ant-... patterns
  tenancy.py       TenantContext + repositories; every query filters by ctx.tenant_id
  security.py      argon2id passwords, JWT access tokens, opaque refresh tokens
  storage.py       object keys tenants/<tenant_id>/... (S3 use comes with #10/#12)
  auth/backend.py  AuthBackend interface (authenticate a bearer token -> Principal)
  auth/local.py    LocalAuthBackend: signup + e-mail verification, login, refresh (rotating), logout
  auth/ratelimit.py  AuthGuard: sliding-window limits and lockout in Postgres (#26)
  invites.py       invitations to a tenant: create (always the same answer), accept
  mail.py          EmailSender interface; dev sender logs to stdout; no SMTP in M1
  purge.py         hwplatform-purge: expired/revoked tokens, old rate-limit rows
  api/             FastAPI app, routes under /api/v1
  migrations/      Alembic; 0001_tenancy creates tables, RLS policies and grants
```

Stack: FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, pydantic v2, argon2-cffi, PyJWT. Postgres 17 from `deploy/compose/platform.yaml`.

Endpoints (`/api/v1`, OpenAPI at `/docs`):

| Method | Path | Who |
|---|---|---|
| POST | `/auth/signup` | anyone: always `202`, same body for new and registered e-mails; the verification token is e-mailed |
| POST | `/auth/verify` | `{token}` from the e-mail: creates user, tenant and owner membership; returns tokens |
| POST | `/auth/login` | `{email, password, tenant_id?}`; `tenant_id` picks one of the user's tenants; `429` when limited or locked |
| POST | `/auth/refresh` | rotates the refresh token |
| POST | `/auth/logout` | revokes the login session (the refresh-token family) |
| GET | `/auth/me` | user, current tenant and role, all tenants of the user |
| GET | `/tenant` | current tenant, incl. the `max_concurrent_runs` quota |
| GET/POST | `/tenant/members` | list; invite an e-mail address (owner/admin; only the owner invites admins): always `202`, same body whatever the address |
| POST | `/auth/accept-invite` | `{token}` from the invitation e-mail, logged in as the invited address: joins the tenant |
| PUT/GET/DELETE | `/tenant/llm-keys/anthropic` | owner/admin: store (validated first), status (never the key), delete the tenant's Claude API key (#14) |
| POST/GET | `/projects` | create, list |
| GET/DELETE | `/projects/{id}` | get, delete; another tenant's project is a 404 |

## Run it

```
cp deploy/compose/.env.example deploy/compose/.env       # set the passwords, incl. HWFLOW_APP_DB_PASSWORD
scripts/platform.sh up
uv sync --all-packages                                   # or: uv sync --package hwplatform

export HWPLATFORM_MIGRATION_DATABASE_URL=postgresql+asyncpg://hwflow:<HWFLOW_DB_PASSWORD>@127.0.0.1:15432/hwflow
export HWPLATFORM_DATABASE_URL=postgresql+asyncpg://hwflow_app:<HWFLOW_APP_DB_PASSWORD>@127.0.0.1:15432/hwflow
export HWPLATFORM_JWT_SECRET=$(openssl rand -hex 32)
export HWPLATFORM_MASTER_KEY=$(openssl rand -base64 32)   # BYOK key encryption; keep it, losing it loses all stored keys
uv run --package hwplatform hwplatform-migrate           # alembic upgrade head, as the owner role
uv run --package hwplatform hwplatform-api               # uvicorn on 127.0.0.1:8080 (HWPLATFORM_HOST/PORT)
```

The `hwflow_app` role is created by `deploy/compose/postgres-init.sh`, which only runs on an empty volume. A volume created before this change lacks it: `scripts/platform.sh down -v && scripts/platform.sh up`, or create it by hand (`CREATE ROLE hwflow_app LOGIN PASSWORD '...'`) and run the migration again.

## Tests

```
cd services/platform
uv run --package hwplatform pytest -q          # unit + integration
uv run --package hwplatform ruff check . && uv run --package hwplatform ruff format --check .
```

Integration tests (marker `integration`) use the compose Postgres, not testcontainers: one Postgres is already part of the dev stack and CI runs the same image as a service container. They read `HWPLATFORM_TEST_OWNER_URL` and `HWPLATFORM_TEST_APP_URL` (plain `postgresql://` URLs; defaults match `.env.example` on port 15432) and skip cleanly when the database does not answer. They reset the schema (`downgrade base`, `upgrade head`) at session start, so never point them at a database you care about. The root `uv run pytest` does not collect them (root `testpaths = ["tests"]`). CI: `.github/workflows/platform.yml`, which fails if the integration tests were skipped.

## Tenancy and row-level security

`tenant_id` is on every tenant-scoped row (memberships, projects, refresh_tokens; later runs, artifacts, keys). Users are global identities and join tenants through memberships with a role `owner | admin | member`.

Isolation is enforced twice:

1. **Query layer.** Routes get a `TenantContext` (tenant, user, role) from the bearer token, and the membership is re-read from the DB on every request (a removed member loses access at once; a token that names a tenant without a membership is refused). Repositories in `tenancy.py` add `tenant_id = ctx.tenant_id` to every query.
2. **Postgres RLS.** `Database.tx()` sets `app.tenant_id`, `app.user_id` and `app.lookup_email` with `set_config(..., true)` (= `SET LOCAL`, gone at commit, so pooled connections do not leak a tenant). All five tables have `ENABLE` and `FORCE ROW LEVEL SECURITY`. The API connects as `hwflow_app`, which owns nothing and is neither superuser nor `BYPASSRLS` (the migration refuses such a role); the migrations run as the owner `hwflow`, and FORCE makes the policies apply to it too.

Policies (`migrations/versions/0001_tenancy.py`), unset context means no rows:

| Table | Visible / writable rows |
|---|---|
| projects, refresh_tokens | `tenant_id = app_tenant_id()` for read and write (`WITH CHECK` blocks writing into another tenant) |
| memberships | read: current tenant's, plus the caller's own in other tenants (login, tenant list); write: current tenant only |
| tenants | read: current one plus those the caller belongs to; insert: only `id = app_tenant_id()`; the app may update `name` only (column grant), not the quota |
| users | read: oneself, one looked-up e-mail (login, add member), members of the current tenant; insert/update: only oneself |

`tests/test_isolation.py` proves both layers: through the API (tenant A cannot list, get or delete B's projects; a validly signed token naming B without a membership gets 401), and with the app filter bypassed (raw SQL as `hwflow_app` with no `WHERE`, ORM queries without a tenant filter, inserts into another tenant, the owner role with no context). Removing RLS from one table makes those tests fail.

A new tenant-scoped table needs, in its migration: a `tenant_id` column, `ENABLE` + `FORCE ROW LEVEL SECURITY`, a `tenant_isolation` policy on `app_tenant_id()`, explicit `GRANT`s to the app role, and a repository that filters by `ctx.tenant_id`.

Object storage: `storage.tenant_key(tenant_id, *segments)` builds `tenants/<tenant_id>/...` keys and rejects `/` and `..` in segments; `key_in_tenant()` checks a key a client sends back.

## Auth flow

- **Passwords:** argon2id (argon2-cffi defaults), rehashed on login when the parameters change. An unknown e-mail costs the same verify time as a wrong password.
- **Access token:** JWT HS256 (`HWPLATFORM_JWT_SECRET`, at least 32 bytes), 15 minutes, claims `sub` (user), `tid` (tenant), `role` (informational), `typ=access`, `iss`. The algorithm is pinned on decode.
- **Refresh token:** opaque `rt.<tenant hex>.<256-bit secret>`, 30 days, stored as SHA-256 in `refresh_tokens`. Each refresh spends the token and issues a new one in the same family. Presenting a spent token revokes the whole family (reuse detection). Logout revokes the family. The tenant id in the token tells the API which RLS context to open for the lookup.
- **Switching tenant:** log in again with `tenant_id`.

## Hardening (#26)

**Rate limits and lockout** (`auth/ratelimit.py`, table `auth_events`, no Redis). Each request on login, signup, refresh and verify appends an event for its e-mail and its client IP; the window count decides. Over the limit gives `429` with `Retry-After`, and the rejected attempt is not recorded. After `lockout_threshold` failed logins for an e-mail (or `lockout_ip_threshold` for an IP) within `lockout_window_s`, the e-mail (IP) is locked even for the right password; the lock ends when the failures leave the window. A successful login clears the e-mail's failures. Limits apply identically to unknown e-mails, so they reveal nothing. Defaults (all `HWPLATFORM_RATE_*`, `HWPLATFORM_LOCKOUT_*`):

| Action | Window | Per e-mail | Per IP |
|---|---|---|---|
| login | 60 s | 10 | 30 |
| refresh, verify | 60 s | | 60 |
| signup | 1 h | 3 | 10 |
| lockout (failed logins) | 15 min | 5 | 50 |

`auth_events` and `pending_signups` are global, not tenant-scoped (their subject is an e-mail or an IP, and the user does not exist yet), so they sit outside the tenant RLS. The app role may insert, read and delete `auth_events` rows but not update them, and only `AuthGuard` writes them. The client IP is the peer address; behind an ingress set uvicorn's `--forwarded-allow-ips` (env `FORWARDED_ALLOW_IPS`) to the proxy, otherwise all clients share one IP. Trade-off: an attacker can lock out a victim's e-mail for 15 minutes by failing on purpose; the per-IP limits bound how many e-mails one source can lock. Counting is check-after-insert in one transaction, so concurrent requests can overshoot a limit by the number in flight.

**No account enumeration.** `POST /auth/signup` is always `202` with the same body. It always runs the argon2 hash and one lookup and sends one e-mail, so status, body and (roughly) timing are equal: a new address gets a verification token, a registered one gets a notice. The account (user, tenant, owner membership) is created only when the token is presented to `POST /auth/verify`, so unverified sign-ups leave no users behind (only a `pending_signups` row, purged after `verify_token_ttl_s`). Login fails with the same `401` for unknown e-mail, wrong password and no membership. `POST /tenant/members` always answers `202` with the same body, whether the address has an account, is a member, or neither; the invitation e-mail carries the token and the invited person presents it (`POST /auth/accept-invite`, logged in as that address). A leaked token alone does not join anyone: the logged-in e-mail must equal the invited one.

**E-mail** (`mail.py`). `EmailSender.send(Email)` is the only interface; verification and invitations use it. M1 has no SMTP: the default `LoggingEmailSender` writes the message, token included, to stdout through the logger (API-key patterns are redacted). Pass a real sender with `create_app(email_sender=...)`.

**Dev flow.** Set `HWPLATFORM_DEV_AUTO_VERIFY=true` and `signup` creates the account at once (the response stays the same `202`), then `login` works:

```
curl -s -XPOST localhost:8080/api/v1/auth/signup -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-password-123","tenant_name":"Acme"}'      # 202
curl -s -XPOST localhost:8080/api/v1/auth/login  -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-password-123"}'                          # tokens
```

Without the flag, read the token from the API's stdout (`E-MAIL to=... ev.<token>`) and `POST /auth/verify {"token": ...}`. Never set the flag in production: it skips proof of ownership of the address.

**Purge.** `hwplatform-purge` (run as the owner role via `HWPLATFORM_MIGRATION_DATABASE_URL`, e.g. as a k8s CronJob once a day) deletes expired refresh tokens, revoked ones older than `purge_revoked_after_s` (7 days: a recently revoked, rotated token is kept so its reuse is still detected), expired or accepted invitations, expired pending sign-ups and `auth_events` older than a day. On the tenant tables the owner role has delete policies for dead rows only, so the job cannot remove a live token or invitation.

**Access-token lifetime and revocation.** Access tokens are stateless JWTs of 15 minutes (`HWPLATFORM_ACCESS_TOKEN_TTL_S`). Logout and refresh-family revocation stop new tokens at once but cannot recall one already issued, which stays valid until `exp` (at most 15 minutes). What does take effect immediately: removing a member (the membership and role are re-read from the DB on every request) and deleting a tenant. A shorter lifetime narrows the window and costs more refreshes; a per-request revocation check (a deny-list or a token-version column read on every request) would close it at the cost of a DB read per request that the membership check already pays, so it can be added later without an API change. 15 minutes is the accepted trade-off for M1.

## BYOK: the tenant's own Claude API key (#14)

Each tenant brings and pays for its own key. The agent worker (#15) calls `get_tenant_llm_key(db, keyring, tenant_id)` and uses only that key; there is no platform-wide key to fall back to.

- **Storage:** table `tenant_llm_keys` (`tenant_id`, `provider='anthropic'`, `ciphertext`, `nonce`, `wrapped_dek`, `key_version`, `last4`, `status`, `created_at`, `rotated_at`, `last_validated_at`), unique per tenant and provider, tenant RLS like the other tenant tables. `llm_usage` (`tenant_id`, `run_id`, `model`, input/output/cache tokens, `at`) is append-only for the app role; `record_usage(db, tenant_id, model, response.usage, run_id=)` fills it from an API `usage` object.
- **Envelope encryption** (`crypto.py`): a random 256-bit DEK per key seals the API key with AES-256-GCM; a master key (KEK) wraps the DEK. Associated data is `tenant_id:provider` for both seals, so a row copied to another tenant fails authentication.
- **Master keys:** `HWPLATFORM_MASTER_KEY` = base64 of 32 bytes (version 1), from a k8s Secret or `.env`. For rotation use a keyring `HWPLATFORM_MASTER_KEYS="2:<b64>,1:<b64>"`: the highest version encrypts, every listed version decrypts. Without a master key the API starts and the key endpoints answer 503.
- **Rotating the master key:** add the new version to `HWPLATFORM_MASTER_KEYS` (keep the old), restart the API (new keys use the new version), run `hwplatform-rotate-master` (re-wraps every DEK; ciphertexts and plaintext keys are untouched), then remove the old version. The command connects with `HWPLATFORM_MIGRATION_DATABASE_URL` (the owner role): the migration gives that role, and only that role, a cross-tenant SELECT/UPDATE policy on `tenant_llm_keys`.
- **Validation:** PUT first calls `GET {HWPLATFORM_ANTHROPIC_API_BASE}/v1/models` with `x-api-key` and `anthropic-version`. 401/403 gives 422 (not stored); network error or other status gives 502 (not stored). Tests inject a fake through `create_app(key_validator=...)`.
- **Never returned, never logged:** responses carry only provider, `last4`, status and timestamps; the request body has no pydantic constraints (so a 422 cannot echo the key); `logredact.install_log_redaction()` replaces `sk-ant-...` in every log record, including exception text.
- Replacing a key sets `rotated_at`; deleting removes the row. `mark_invalid()` lets the worker flag a key the provider refused (status `invalid`, `get_tenant_llm_key` then returns None).
- Not done here: exhausted-key/rate-limit retry in the workflow (that belongs to the worker, #15).

## Swapping for OIDC (Keycloak, M2)

The API depends only on `AuthBackend.authenticate(bearer) -> Principal(user_id, tenant_id)`. An `OidcAuthBackend` validates the IdP's JWT (JWKS, issuer, audience), maps `sub` to a `users` row (create on first sight, `password_hash` NULL) and a tenant claim (or a tenant header checked against memberships) to `tenant_id`, and sets `issues_tokens = False`. Pass it as `create_app(auth=OidcAuthBackend(...))`: the signup, login, refresh and logout routes are then not mounted, `/auth/me` stays, and everything below the `TenantContext` (membership check, repositories, RLS) is unchanged.

## Open points

- No e-mail delivery: the dev sender logs to stdout. A real `EmailSender` (SMTP or provider API) is needed before sign-up is exposed.
- Password reset and e-mail change are not implemented.
- Invitation creation is not rate limited beyond the admin role requirement.
- After logout an access token stays valid until it expires (15 min, see Hardening). A removed member is refused at once.
- The OIDC mode does not mount the sign-up, verify and accept-invite routes; mapping invitations to IdP users is an M2 task.
