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
  auth/local.py    LocalAuthBackend: signup, login, refresh (rotating), logout
  api/             FastAPI app, routes under /api/v1
  migrations/      Alembic; 0001_tenancy creates tables, RLS policies and grants
```

Stack: FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, pydantic v2, argon2-cffi, PyJWT. Postgres 17 from `deploy/compose/platform.yaml`.

Endpoints (`/api/v1`, OpenAPI at `/docs`):

| Method | Path | Who |
|---|---|---|
| POST | `/auth/signup` | anyone: creates a user, a tenant and an owner membership; returns tokens |
| POST | `/auth/login` | `{email, password, tenant_id?}`; `tenant_id` picks one of the user's tenants |
| POST | `/auth/refresh` | rotates the refresh token |
| POST | `/auth/logout` | revokes the login session (the refresh-token family) |
| GET | `/auth/me` | user, current tenant and role, all tenants of the user |
| GET | `/tenant` | current tenant, incl. the `max_concurrent_runs` quota |
| GET/POST | `/tenant/members` | list; add an existing user by e-mail (owner/admin; only the owner adds admins). No e-mail is sent. |
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

- No rate limiting or lockout on login and signup yet; signup and add-member reveal whether an e-mail is registered.
- No e-mail verification or invitation e-mails; add-member needs an existing account.
- After logout an access token stays valid until it expires (15 min). A member removed from a tenant is refused at once by the per-request membership check.
- Expired and revoked refresh tokens are not purged yet.
