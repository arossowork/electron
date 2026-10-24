# hwplatform: the hosted multi-tenant service

The platform side of epic #21. It is a uv workspace member, separate from the engine: `src/hwflow` knows nothing about tenants, users or HTTP. This package holds the tenancy data model and the built-in auth (#13); the API for designs, runs and artifacts (#12) and BYOK keys (#14) build on it.

## Architecture

```
src/hwplatform/
  settings.py      pydantic-settings, env prefix HWPLATFORM_
  db.py            async engine; Database.tx(tenant_id, user_id, lookup_email) = one transaction with the RLS context
  models.py        tenants, users, memberships, projects, refresh_tokens (SQLAlchemy 2)
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

## Swapping for OIDC (Keycloak, M2)

The API depends only on `AuthBackend.authenticate(bearer) -> Principal(user_id, tenant_id)`. An `OidcAuthBackend` validates the IdP's JWT (JWKS, issuer, audience), maps `sub` to a `users` row (create on first sight, `password_hash` NULL) and a tenant claim (or a tenant header checked against memberships) to `tenant_id`, and sets `issues_tokens = False`. Pass it as `create_app(auth=OidcAuthBackend(...))`: the signup, login, refresh and logout routes are then not mounted, `/auth/me` stays, and everything below the `TenantContext` (membership check, repositories, RLS) is unchanged.

## Open points

- No rate limiting or lockout on login and signup yet; signup and add-member reveal whether an e-mail is registered.
- No e-mail verification or invitation e-mails; add-member needs an existing account.
- After logout an access token stays valid until it expires (15 min). A member removed from a tenant is refused at once by the per-request membership check.
- Expired and revoked refresh tokens are not purged yet.
