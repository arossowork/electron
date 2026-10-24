# hwplatform: the hosted multi-tenant service

The platform side of epic #21. It is a uv workspace member, separate from the engine: `src/hwflow` knows nothing about tenants, users or HTTP. This package holds the tenancy data model and the built-in auth (#13); the API for designs, runs, artifacts, questions and events (#12) and BYOK keys (#14) build on it.

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
  bootstrap.py     hwplatform-migrate extras: bucket CORS rule, Temporal search attributes (idempotent)
  tenancy.py       TenantContext + repositories; every query filters by ctx.tenant_id
  security.py      argon2id passwords, JWT access tokens, opaque refresh tokens
  storage.py       object keys tenants/<tenant_id>/...; S3ArtifactStore (boto3), one instance per tenant (#6)
  contracts.py     ArtifactRef/ArtifactStore and Run/RunStore types from ADR 0001 (until hwflow.artifacts/runs land)
  runs.py          PgRunStore: runs, stages, artifact refs, project state, manifest export (#7)
  ids.py           uuid7()
  events/          Event (CloudEvents envelope), outbox.emit(), relay.Relay (outbox -> NATS JetStream) (#11), sse (SSE streams, #12)
  gateway.py       WorkflowGateway: start/cancel/signal Temporal workflows (TemporalGateway; tests use a fake) (#12)
  designs.py       design versions from an upload: files + tree artifact in S3, content-addressed (#12)
  questions.py     QuestionStore: ask / answer the questions of a run (#12)
  run_options.py   allowlist and normalization of run options, options_hash (#12)
  auth/backend.py  AuthBackend interface (authenticate a bearer token -> Principal)
  auth/local.py    LocalAuthBackend: signup + e-mail verification, login, refresh (rotating), logout
  auth/ratelimit.py  AuthGuard: sliding-window limits and lockout in Postgres (#26)
  invites.py       invitations to a tenant: create (always the same answer), accept
  mail.py          EmailSender interface; dev sender logs to stdout; no SMTP in M1
  purge.py         hwplatform-purge: expired/revoked tokens, old rate-limit rows
  api/             FastAPI app, routes under /api/v1
  migrations/      Alembic; 0001_tenancy creates tables, RLS policies and grants; 0004_runs, 0005_outbox, 0006_questions (#7, #11, #12)
```

Stack: FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, pydantic v2, argon2-cffi, PyJWT, boto3 (S3), nats-py (JetStream). Postgres 17, SeaweedFS and NATS from `deploy/compose/platform.yaml`.

Endpoints (`/api/v1`, OpenAPI at `/docs`):

| Method | Path | Who |
|---|---|---|
| POST | `/auth/signup` | anyone: always `202`, same body for new and registered e-mails; the verification token is e-mailed |
| POST | `/auth/verify` | `{token}` from the e-mail: creates user, tenant and owner membership; returns tokens |
| POST | `/auth/login` | `{email, password, tenant_id?}`; `tenant_id` picks one of the user's tenants; `429` when limited or locked |
| POST | `/auth/refresh` | rotates the refresh token |
| POST | `/auth/switch-tenant` | body `{refresh_token, tenant_id}`: spends the refresh token like `/auth/refresh` and returns tokens for another tenant the user is a member of (`403` if not a member, the token stays valid; `401` invalid or reused token; same rate limit as refresh) |
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
export HWPLATFORM_RELAY_DATABASE_URL=postgresql+asyncpg://hwflow_relay:<HWFLOW_RELAY_DB_PASSWORD>@127.0.0.1:15432/hwflow
export HWPLATFORM_S3_ENDPOINT_URL=http://127.0.0.1:8333 HWPLATFORM_S3_ACCESS_KEY=<S3_ACCESS_KEY> HWPLATFORM_S3_SECRET_KEY=<S3_SECRET_KEY>
export HWPLATFORM_NATS_URL=nats://127.0.0.1:4222
uv run --package hwplatform hwplatform-migrate           # alembic upgrade head (owner role) + creates the JetStream stream
uv run --package hwplatform hwplatform-api               # uvicorn on 127.0.0.1:8080 (HWPLATFORM_HOST/PORT); runs the outbox relay
uv run --package hwplatform hwplatform-relay             # the relay alone (then HWPLATFORM_RELAY_ENABLED=false on the API)
```

The `hwflow_app` and `hwflow_relay` roles are created by `deploy/compose/postgres-init.sh`, which only runs on an empty volume (set `HWFLOW_RELAY_DB_PASSWORD` in `.env`, see `.env.example`). A volume created before this change lacks them: `scripts/platform.sh down -v && scripts/platform.sh up`, or create them by hand (`CREATE ROLE hwflow_app LOGIN PASSWORD '...'`, same for `hwflow_relay`) and run the migration again.

## Tests

```
cd services/platform
uv run --package hwplatform pytest -q          # unit + integration
uv run --package hwplatform ruff check . && uv run --package hwplatform ruff format --check .
```

Integration tests (marker `integration`) use the compose stack, not testcontainers: Postgres, SeaweedFS (S3) and NATS are already part of the dev stack and CI starts the same images. They read `HWPLATFORM_TEST_OWNER_URL`, `HWPLATFORM_TEST_APP_URL`, `HWPLATFORM_TEST_RELAY_URL` (plain `postgresql://` URLs; defaults match `.env.example` on port 15432), `HWPLATFORM_TEST_S3_ENDPOINT` / `_S3_ACCESS_KEY` / `_S3_SECRET_KEY` / `_S3_BUCKET` and `HWPLATFORM_TEST_NATS_URL` (defaults: the `.env.example` values, ports 8333 and 4222), and skip cleanly when the service does not answer (the bucket is created if missing). They reset the schema (`downgrade base`, `upgrade head`) at session start, so never point them at a database you care about. The root `uv run pytest` does not collect them (root `testpaths = ["tests"]`). CI: `.github/workflows/platform.yml`, which fails if the integration tests were skipped.

## Builds: BuildWorkflow and hwplatform-worker (#9)

`POST /projects/{id}/runs` starts `BuildWorkflow` (`hwplatform.workflows`) on the Temporal queue `hwflow-engine`. The workflow drives the engine's planner (`hwflow.buildplan.plan`, the same one the inline CLI uses). It records each run and stage change through local activities, and each change commits its row and its outbox event in one transaction (`run.*`, `stage.*`). Each stage is `run_stage_activity` on the queue of its tools (`contracts.QUEUE_OF`). That activity runs the engine's `execute_stage` in a child process (`python -m hwplatform.activities.child`, own process group) with the tenant's S3 store and the Postgres stage cache, and heartbeats every 15 s. Layout attempts run in parallel; the planner cancels the ones past the cutoff or the budget. `render_run` writes status.json, REPORT.md and score.json as run artifacts (the same view as the CLI). Retries and timeouts follow ADR 0001 4.5 (`contracts.STAGE_POLICY`). A cancel kills the stage children before the run is marked `cancelled`.

```
export HWPLATFORM_TEMPORAL_ADDRESS=127.0.0.1:7233        # plus the database and S3 variables above
uv run --package hwplatform hwplatform-worker            # every queue it can serve; --queues or HWPLATFORM_WORKER_QUEUES to pick
curl -s localhost:8081/readyz                            # 200 when connected and polling (HWPLATFORM_WORKER_HEALTH_PORT)
```

The worker needs the engine's tools (kicad-cli and the KiCad libraries, ngspice, KRT or Freerouting for the layout). A stage whose tools are missing fails with `ToolMissing` and is not retried. From the CLI, `HWPLATFORM_API_URL=... HWPLATFORM_TOKEN=<access token> uv run hwflow build <proj> --remote [--detach]` uploads the folder, follows the run and checks REPORT.md and status.json out into `build/`. `hwflow run wait|status <proj> --remote` read the run.

Tests: `tests/test_workflow_build.py` covers the workflow in the time-skipping test server with fake activities (stage order, hard-failure stop, parallel attempts and select, retry, no retry, cancel). `tests/test_build_e2e.py` runs a real build of `examples/sensor_preamp` through API, Temporal dev server, worker process, S3 and SSE. It checks that the platform's REPORT.md equals the inline one, kills the worker mid-stage and checks the run still completes, and drives the remote executor. It runs up to `sim` by default; `HWPLATFORM_TEST_FULL_BUILD=1` runs the full build with the layout.

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

## Object storage: S3ArtifactStore (#6)

`storage.S3ArtifactStore(client, bucket, tenant_id)` implements the `ArtifactStore` protocol of ADR 0001, section 2 on S3 (SeaweedFS in dev, any S3 in production). One instance is bound to one tenant and has no tenant argument, so no call can name another tenant's prefix. Objects live at `tenants/<tenant_id>/artifacts/<sha256>` (built with `tenant_key`); identical bytes of two tenants are two objects. boto3 is used because the protocol is synchronous (activities call it in threads); `make_s3_client(settings)` builds the client (path-style addressing, `HWPLATFORM_S3_*`).

- `put_bytes` / `put_file`: hash, `HeadObject`, upload only if absent, `ContentType` set on the first put (`stat` returns size and media type as first stored). `put_file` reads 8 MiB chunks and uses multipart above 64 MiB (parts of 8 MiB); the upload stream is hashed again on the wire and the object is deleted if the file changed meanwhile. Nothing loads a large file in memory.
- `expected_sha256=` on both puts: a mismatch raises `ArtifactHashMismatch` and stores nothing.
- `open(sha)` streams the body (`ArtifactNotFound` if absent); `get_file(sha, dest)` downloads to a temp file in the target directory, verifies the hash, then renames (a corrupt object raises `ArtifactHashMismatch` and leaves nothing behind).
- `presigned_get_url(sha, expires_s=900, filename=)`: a time-limited GET URL so downloads bypass the API.
- Network errors and 5xx raise `StoreUnavailable` (retryable); 404 is "absent".

The activity that collects stage outputs registers them in the `artifacts` table (`PgRunStore.register_artifacts`; `upsert_stage` and `set_artifacts` do it too). Not done here (engine side, #6): `hwflow.artifacts` (protocol, `LocalCAS`, trees), the stage declaration and the cache key, `execute_stage`, and the "build passes against S3" test that runs `execute_stage` with this store.

## Runs: PgRunStore (#7)

Migration `0004_runs` creates `design_versions`, `artifacts`, `runs`, `run_stages`, `run_artifacts`, `stage_cache` and `project_state` as in ADR section 3.4 (enums `run_kind`, `run_status`; partial unique index `ux_runs_active_workflow`), each with `tenant_id`, `ENABLE` + `FORCE` RLS, policy `tenant_isolation` and grants to the app role (the app never deletes: rows go with their project or tenant). `contracts.py` holds the ADR types (`Run`, `RunStatus`, `TRANSITIONS`, `StageRecord`, ...) until `hwflow.runs` exists; then it re-exports them.

`runs.PgRunStore(db, tenant_id)` is async and bound to one tenant; every transaction goes through `Database.tx(tenant_id=)`.

| Method | Notes |
|---|---|
| `create(run, idempotency_key=, requested_by=)` | UUIDv7 id (`ids.uuid7`), state `queued`, emits `run.queued`; a repeated key returns the first run |
| `transition(id, to, error=, verdict=, event_data=)` | one `UPDATE ... WHERE status = ANY(allowed_from)`; 0 rows = `InvalidTransition` (or `RunNotFound`); emits `run.started/waiting/resumed/completed/failed/cancelled` |
| `upsert_stage(id, rec)` | idempotent on `(run_id, key)`; emits `stage.started/completed/failed/skipped` on a status change; a late replay of an earlier state cannot undo a finished stage |
| `set_artifacts(id, {name: ref})` | `run_artifacts` rows and `runs.artifacts`; `artifact.created` for REPORT.md, board, fab, manifest.json |
| `append_event(id, type, data)` | catalogue event in its own transaction, returns `seq` |
| `get`, `list(project, limit, before)` | newest first (UUIDv7 order) |
| `project_state`, `set_project_state(project, state, run_id)` | an older run never overwrites a newer one |
| `create_design_version(...)` | per-project `seq`, idempotent on the content hash, emits `design_version.created` |
| `manifest(id)`, `store_manifest(id, artifact_store)` | `hwflow.run/1` JSON rendered from the rows (`dataclasses.asdict` plus `"schema"`, RFC 3339 UTC, stages in `seq` order), stored as run artifact `manifest.json` |

`status` is about execution; `verdict` (PASS/FAIL) is separate, as in the ADR. Not done here: the JSON Schema `docs/schemas/run-1.json`, `FileRunStore`, `render_status` and `checkout` (engine side, #7), `stage_cache` access (comes with the cache key, #6), the concurrency quota (#19).

## Events and the outbox (#11)

Migration `0005_outbox`: `outbox` (bigserial id, unique `event_id`, tenant, run, `seq`, type, NATS subject, envelope jsonb, `published_at`), `consumed_events` (consumer idempotence) and a statement trigger that does `pg_notify('outbox')`. RLS: `tenant_isolation` for the app role, which may insert and read but never update or delete. The relay connects as the extra role `hwflow_relay` (LOGIN, no BYPASSRLS, created by `postgres-init.sh`): policies `relay_read` and `relay_mark` let it read every tenant's rows and set `published_at`, with grants `SELECT` and `UPDATE (published_at)` only, so it cannot read runs or change an event. The owner role may delete published rows only (`hwplatform-purge` removes them after `HWPLATFORM_OUTBOX_PURGE_AFTER_S`, 30 days).

- **Writing.** `events.outbox.emit(session, tenant_id, run_id, type, subject, data)` runs in the caller's session: it bumps `runs.event_seq` under the row lock (a gapless `seq` per run) and inserts the row. `PgRunStore` calls it in the same transaction as every state change, so a rolled-back change leaves no event and a committed one always has it. `events.Event` builds the CloudEvents 1.0 envelope (`specversion`, `id` UUIDv7, `type`, `source`, `subject`, `time`, `dataschema` `hwflow.events/<type>/1`, extensions `tenantid`, `runid`, `seq`, `data`); types outside the ADR 5.2 catalogue are refused.
- **Relay** (`events.relay.Relay`). Leader election with `pg_try_advisory_lock(hashtext('hwflow.outbox.relay'))` on its own connection (one relay publishes; the lock frees when the leader dies). It wakes on `LISTEN outbox` or every `HWPLATFORM_RELAY_POLL_S` (1 s), reads up to `HWPLATFORM_RELAY_BATCH` (100) unpublished rows `FOR UPDATE SKIP LOCKED` in id order, publishes each to `hwflow.<tenant_id>.<type>` with header `Nats-Msg-Id: <event_id>`, waits for the JetStream ack and sets `published_at`. A crash between the ack and the UPDATE republishes after restart, and the stream drops the duplicate (duplicate window 2 h): at-least-once delivery, one copy in the stream. Errors reconnect with backoff. It runs inside `hwplatform-api` as a background task started and stopped by the lifespan hook (`HWPLATFORM_RELAY_ENABLED=false` disables it), and standalone as `hwplatform-relay`.
- **Stream** `HWFLOW_EVENTS`: subjects `hwflow.>`, file storage, limits retention, 7 days, 512 MiB, discard old, duplicate window 2 h, 1 replica. Created or updated idempotently by `hwplatform-migrate` (it exits 1 if NATS is unreachable after migrating; an empty `HWPLATFORM_NATS_URL` skips it) and by the relay at start.
- **Bootstrap in `hwplatform-migrate`** (`bootstrap.py`, after the stream; each step exits 1 on failure, runs any number of times): the CORS rule `GET`/`HEAD` on the artifact bucket for `HWPLATFORM_WEB_ORIGINS` (comma separated; empty skips it) so a browser can follow a presigned download, and the Temporal search attributes `HwTenantId`, `HwProjectId`, `HwRunId`, `HwRunKind` (Keyword) in `HWPLATFORM_TEMPORAL_NAMESPACE`, added through the operator service when missing (an empty `HWPLATFORM_TEMPORAL_ADDRESS` skips it). The bucket must exist already (the bucket job creates it).
- **Ordering caveat.** Rows are published in `id` order, but `id` is assigned at INSERT; a transaction with a lower id can commit after a higher one, so a consumer can see two events of a run swapped. The per-run `seq` is gapless and authoritative: the SSE replay (#12) reads the outbox ordered by `seq`.

Not done here: the durable consumers (`notifier`, `eval-scorer`, #19). The SSE bridge is in the API section below.

## API: design versions, runs, artifacts, questions, events (#12)

Everything is under `/api/v1`, bearer JWT, tenant from the token (membership re-read per request). Another tenant's object is a `404`, never a `403`. Lists of design versions and runs are cursor-paged: `?limit=50&cursor=<id>` returns `{items, next_cursor}` (UUIDv7 ids sort by time, newest first). The OpenAPI document is `/openapi.json` (Swagger UI at `/docs`); the copy committed as `services/platform/openapi.json` is checked by `tests/test_openapi.py`. After an API change run `uv run --package hwplatform python -m hwplatform.api.openapi` and commit the result.

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/projects/{pid}/design-versions` | member | multipart: `design` (design.yaml), `requirements`, `notes` (many), `bundle` (tar.gz, entries validated, 50 MiB uncompressed cap); explicit parts override bundle entries. `201` new version, `200` existing one when the content hash matches. Only YAML syntax is checked (`422`); `413` over the cap |
| GET | `/projects/{pid}/design-versions`, `/design-versions/{id}` | member | list (paged); metadata + file list |
| GET | `/design-versions/{id}/files/{path}` | member | `302` to a presigned URL (the bytes when `HWPLATFORM_S3_PUBLIC_URL` is unset) |
| POST | `/projects/{pid}/runs` | member | `{kind: build\|design, design_version_id, options, settings_overrides?}` and optional `Idempotency-Key`. `202` queued; `200` when the same build is already active or the key was used; `429 quota_exceeded`; `422` for options outside the allowlist; `503` when Temporal is down (the run is marked `failed`) |
| GET | `/projects/{pid}/runs?kind=&status=` | member | summaries, paged |
| GET | `/runs/{id}` | member | run with `stages[]`, `verdict`, `error` |
| POST | `/runs/{id}/cancel` | member | `202`; `409` when already finished. Signals the workflow if it exists; a run still `queued` (no workflow task has started it, e.g. no worker) is marked `cancelled` at once with `run.cancelled`; a `running` one is left to the workflow (ADR 4.6) |
| GET | `/runs/{id}/manifest`, `/runs/{id}/report` | member | `hwflow.run/1` JSON rendered live; `REPORT.md` as `text/markdown` |
| GET | `/runs/{id}/artifacts`, `/runs/{id}/artifacts/{name}` | member | list; `302` to a presigned GET (TTL `HWPLATFORM_S3_DOWNLOAD_URL_TTL_S`, 300 s) |
| GET | `/runs/{id}/questions?status=` | member | open and answered questions |
| POST | `/questions/{qid}/answer` | member | `{answers: {id: value}}`; `422` unknown id or missing answer without a default; `409` not open or run ended; writes the answer, emits `question.answered`, then signals the workflow (signal failure: `503`, the question stays open) |
| GET | `/runs/{id}/events`, `/projects/{pid}/events`, `/events` | member | SSE, see below |
| DELETE | `/projects/{pid}` | owner/admin | members get `403` |

**Starting a run.** In one transaction under a row lock on the tenant, `PgRunStore.admit` (1) returns the run with the same `Idempotency-Key`, (2) returns the active run with the same workflow ID (`build-<tenant>-<project>-<content_sha256[:16]>-<options_hash>`), (3) answers `429` when the tenant has `max_concurrent_runs` active top-level runs, (4) else inserts the run as `queued` (+ `run.queued`). Then the `WorkflowGateway` starts `BuildWorkflow` (ID reuse `ALLOW_DUPLICATE`, conflict policy `USE_EXISTING`, task queue `HWPLATFORM_TEMPORAL_ENGINE_QUEUE`, argument `contracts.BuildInput`, search attributes `HwTenantId`, `HwProjectId`, `HwRunId`, `HwRunKind`, which the namespace must have registered). Build options: `until, mc, reuse, force, rebuild, auto, attempts, retry_layout, seeds, iters, passes, brief` (defaults filled, so the same request always has the same `options_hash`); `settings_overrides`: `layout.budget_s`, `layout.seed0`, `layout.asic.*`. A design run takes `{brief, answerer: "human", stages?}`. The workflows are issue #9; until they run, a started run stays `queued`.

**SSE** (`text/event-stream`). The outbox is the source of truth, NATS JetStream the low-latency path. A stream subscribes to an ephemeral ordered consumer on `hwflow.<tenant>.>` first, then replays from the outbox after `Last-Event-ID` (header, or `?last_event_id=`), then follows live: the next `seq` is sent as it arrives, a duplicate is dropped, a gap is filled from the outbox, and the outbox is also re-read every `HWPLATFORM_SSE_POLL_S` (5 s), so a stream still works when NATS is down. `/runs/{id}/events`: `id:` is the per-run `seq`, `event:` the type, `data:` the CloudEvents envelope; it ends after `run.completed|failed|cancelled`. `/projects/{pid}/events` and `/events` (the whole tenant): `id:` is the outbox id; without `Last-Event-ID` they start at the current end. A `: keep-alive` comment goes out every 15 s (`HWPLATFORM_SSE_KEEPALIVE_S`). Browsers cannot set an `Authorization` header on `EventSource`; use `fetch` streaming (or a polyfill).

**Questions** (`0006_questions`, RLS like the other run tables). The worker calls `QuestionStore.ask(run_id, stage, questions)` (row + `question.asked`); the API answers. `tests/test_questions_rls.py` proves isolation with raw SQL.

**Downloads.** The API checks the `run_artifacts` / `artifacts` row under RLS, builds the key from the caller's tenant (`tenant_key`), and redirects to a presigned URL signed for `HWPLATFORM_S3_PUBLIC_URL` (the browser-reachable SeaweedFS). Unset (dev): the API streams the object.

Tests: `tests/test_api_runs.py` (HTTP, fake gateway: versions, start, idempotency, quota, cancel, artifacts, questions, roles, cross-tenant 404 on every endpoint) and `tests/test_api_e2e.py` (a real uvicorn server with the relay: create project, upload, start, follow SSE replay and live in `seq` order, presigned download, answer a question, check the signal). `tests/test_gateway_temporal.py` runs `TemporalGateway` against the temporalio dev server (downloaded once) with stand-in workflows named like the ADR's: start with the workflow ID, `USE_EXISTING`, the answer signal, cancel, not found. Config added: `HWPLATFORM_S3_PUBLIC_URL`, `S3_DOWNLOAD_URL_TTL_S`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_ENGINE_QUEUE`, `MAX_BUNDLE_BYTES`, `MAX_BUNDLE_FILES`, `SSE_KEEPALIVE_S`, `SSE_POLL_S`.

Not done here: `POST /evals` (EvalWorkflow, #19), the `PUT /tenant/llm-keys` signal `retry` to runs waiting on a key (#15), `fab.zip` creation (the `render_run` activity, #9), the workflows themselves (#9).

## Auth flow

- **Passwords:** argon2id (argon2-cffi defaults), rehashed on login when the parameters change. An unknown e-mail costs the same verify time as a wrong password.
- **Access token:** JWT HS256 (`HWPLATFORM_JWT_SECRET`, at least 32 bytes), 15 minutes, claims `sub` (user), `tid` (tenant), `role` (informational), `typ=access`, `iss`. The algorithm is pinned on decode.
- **Refresh token:** opaque `rt.<tenant hex>.<256-bit secret>`, 30 days, stored as SHA-256 in `refresh_tokens`. Each refresh spends the token and issues a new one in the same family. Presenting a spent token revokes the whole family (reuse detection). Logout revokes the family. The tenant id in the token tells the API which RLS context to open for the lookup.
- **Switching tenant:** log in again with `tenant_id`.

## Hardening (#26)

**Rate limits and lockout** (`auth/ratelimit.py`, table `auth_events`, no Redis). Each request on login, signup, refresh, switch-tenant and verify appends an event for its e-mail and its client IP; the window count decides. Over the limit gives `429` with `Retry-After`, and the rejected attempt is not recorded. After `lockout_threshold` failed logins for an e-mail (or `lockout_ip_threshold` for an IP) within `lockout_window_s`, the e-mail (IP) is locked even for the right password; the lock ends when the failures leave the window. A successful login clears the e-mail's failures. Limits apply identically to unknown e-mails, so they reveal nothing. Defaults (all `HWPLATFORM_RATE_*`, `HWPLATFORM_LOCKOUT_*`):

| Action | Window | Per e-mail | Per IP |
|---|---|---|---|
| login | 60 s | 10 | 30 |
| refresh, switch-tenant, verify | 60 s | | 60 |
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

Each tenant brings and pays for its own key. The agent worker (#15) resolves it through `hwplatform.credentials` and uses only that key; the only other credential is the operator's own, for the operator's own tenants ("Agent credentials" below).

- **Storage:** table `tenant_llm_keys` (`tenant_id`, `provider='anthropic'`, `ciphertext`, `nonce`, `wrapped_dek`, `key_version`, `last4`, `status`, `created_at`, `rotated_at`, `last_validated_at`), unique per tenant and provider, tenant RLS like the other tenant tables. `llm_usage` (`tenant_id`, `run_id`, `model`, input/output/cache tokens, `at`) is append-only for the app role; `record_usage(db, tenant_id, model, response.usage, run_id=)` fills it from an API `usage` object.
- **Envelope encryption** (`crypto.py`): a random 256-bit DEK per key seals the API key with AES-256-GCM; a master key (KEK) wraps the DEK. Associated data is `tenant_id:provider` for both seals, so a row copied to another tenant fails authentication.
- **Master keys:** `HWPLATFORM_MASTER_KEY` = base64 of 32 bytes (version 1), from a k8s Secret or `.env`. For rotation use a keyring `HWPLATFORM_MASTER_KEYS="2:<b64>,1:<b64>"`: the highest version encrypts, every listed version decrypts. Without a master key the API starts and the key endpoints answer 503.
- **Rotating the master key:** add the new version to `HWPLATFORM_MASTER_KEYS` (keep the old), restart the API (new keys use the new version), run `hwplatform-rotate-master` (re-wraps every DEK; ciphertexts and plaintext keys are untouched), then remove the old version. The command connects with `HWPLATFORM_MIGRATION_DATABASE_URL` (the owner role): the migration gives that role, and only that role, a cross-tenant SELECT/UPDATE policy on `tenant_llm_keys`.
- **Validation:** PUT first calls `GET {HWPLATFORM_ANTHROPIC_API_BASE}/v1/models` with `x-api-key` and `anthropic-version`. 401/403 gives 422 (not stored); network error or other status gives 502 (not stored). Tests inject a fake through `create_app(key_validator=...)`.
- **Never returned, never logged:** responses carry only provider, `last4`, status and timestamps; the request body has no pydantic constraints (so a 422 cannot echo the key); `logredact.install_log_redaction()` replaces `sk-ant-...` in every log record, including exception text.
- Replacing a key sets `rotated_at`; deleting removes the row. `mark_invalid()` lets the worker flag a key the provider refused (status `invalid`, `get_tenant_llm_key` then returns None).
- Invalid, exhausted and rate-limited keys during a design run: see the design agent section below (#15).

## Design agent: DesignWorkflow and the agent worker (#15)

`POST /projects/{id}/runs` with `kind=design` and `options={brief, stages?}` starts `DesignWorkflow` (ADR 0001, 7 and
7.6). It runs the stages `requirements -> partition -> parts -> circuit -> review -> layout -> firmware` (or the
listed subset). Each stage is one `agent_stage` activity on `hwflow-agent`: one Claude Agent SDK session whose
system prompt is the stage's `.claude/agents/*.md` files plus the `/hw-design` skill and `CLAUDE.md`, built by
`hwharness.prompts` (the local harness's protocol, `inbox/` and `claude -p`, is unchanged). The model of a stage
is the agent frontmatter's (Opus 5.5 `claude-opus-5-5`, Sonnet 5.5 `claude-sonnet-5-5`, Haiku 4.5
`claude-haiku-4-5-20251001`), overridable with `HWPLATFORM_AGENT_MODELS="layout=sonnet,circuit=claude-opus-5-5"`.
`HWPLATFORM_AGENT_MAX_BUDGET_USD` caps one session's spend.

- **Tools.** The session has no shell. Its engine actions are the MCP tools of the in-process server `hwflow`
  (`hwplatform.agent.tools`): `check` and `build` start a linked `BuildWorkflow` (its own run row,
  `parent_run_id` = the design run, so no quota slot) and wait for it; `lib_*`, `parts_search`, `trace`, `fw`,
  `floorplan`, `optimize` and `fetch_url` run as `EngineToolWorkflow` on the engine queue, because the agent
  image has no KiCad and no general internet access. `ask_product_person` and `log_decision` replace
  `inbox/questions.json` and `answers.md`.
- **Questions.** `ask_product_person` ends the session's turn; the workflow saves the workspace as a design
  version, writes the `questions` row (`question.asked`, run `waiting`) and waits on the `answer_question` signal
  that `POST /questions/{id}/answer` sends, with no timeout unless `HWPLATFORM_AGENT_QUESTION_TIMEOUT_S` is set.
  The same session then resumes from its saved transcript with the answers.
- **Credentials.** The activity resolves the session's credential through the credential port (see "Agent
  credentials" below); it exists only in the CLI subprocess's environment. Usage is recorded per model after each
  result. A rejected or exhausted key: `mark_invalid`, run `waiting` (reason `llm_key_invalid`) until
  `PUT /tenant/llm-keys/anthropic` sends `retry` (72 h at most). A rate limit: a workflow timer of the reset time
  the credential's adapter gives (the API's, or a subscription window's, up to 24 h per timer), else 60 s doubling
  to 30 min; a `retry` signal ends the wait early. Signals and timers only, no polling.
- **Resume.** Every 5 min and at the end of a session the activity stores the workspace tree and the transcript
  (`application/x-hwflow-agent-session+tar.gz`) in S3 and heartbeats their refs. A stage cut by a worker restart
  is retried by Temporal and resumes the same session from the last snapshot; finished stages are not run again.
  Each stage end that changed files saves a design version (source `agent`).
- **Worker.** `hwplatform-worker --queues hwflow-agent` in the image `deploy/images/agent.Dockerfile` (Python, the
  SDK with its bundled native Claude Code binary, no Node, no KiCad, no engine; 914 MB uncompressed, 291 MB of it
  the CLI). A worker without the SDK drops the agent queue at start and says so. Measured in that image: the worker
  process idles at about 112 MiB, a Claude Code session starts at about 200 MiB and grows with its context. Helm
  runs it as its own Deployment in both modes (`agent.*` values): sharing the reduced-mode worker pod would save
  about 112 MiB but put the session next to the build tools, the scratch of every build and the wider worker
  egress, and would need the SDK in the 2.9 GB worker image.

### Agent credentials: one port, two adapters (#15)

`hwplatform.credentials.CredentialService` is the port: `resolve(tenant_id)` gives the session's credential (its
CLI environment comes from `Credential.env()`), `put` validates and stores one, `mark_invalid`, `record_usage`, and
`limit_wait` turns a limit message into a wait. The adapters:

| Type | Secret | CLI environment | Validation | Limit wait |
|---|---|---|---|---|
| `api_key` (default, every tenant) | Anthropic API key `sk-ant-api...` | `ANTHROPIC_API_KEY` | `GET /v1/models` (#14) | the API's reset time, else the workflow's backoff |
| `subscription_token` (operator tenants only) | `claude setup-token` OAuth token `sk-ant-oat...` | `CLAUDE_CODE_OAUTH_TOKEN` | format only by default (no documented cheap check; the first session's auth error marks it invalid); injectable (`create_app(token_validator=...)`) | the window's reset from the CLI's usage-limit message (`hwharness.limits`, the local harness's parser) |

A session gets exactly one of the two variables: the other is blank and the launcher drops it, so a worker-level
`ANTHROPIC_API_KEY` never rides along with a token. `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR` and
`ANTHROPIC_AUTH_TOKEN` are never inherited.

- **Storage.** The same `tenant_llm_keys` row and envelope encryption, plus `credential_type` (migration 0007):
  still one active credential per tenant and provider. `PUT /tenant/llm-keys/anthropic` takes
  `{"api_key": "...", "credential_type": "api_key" | "subscription_token"}` and validates with that type's
  adapter; GET shows `credential_type` and `last4` only. `logredact` redacts both formats (`sk-ant-...`).
- **Policy and the gate.** The Agent SDK terms: "Unless previously approved, Anthropic does not allow third party
  developers to offer claude.ai login or rate limits for their products, including agents built on the Claude
  Agent SDK. Use the API key authentication methods described in the Quickstart instead." So subscription
  credentials are for the operator's own tenants only: `HWPLATFORM_SUBSCRIPTION_TENANT_IDS` (comma-separated
  tenant UUIDs; empty, the default, = none). Any other tenant gets 403 on PUT of a `subscription_token`, and a
  subscription row of a tenant that is not listed (any more) is never resolved: the gate is checked on every
  resolve, not only on PUT.
- **The operator credential (`k8s_secret`).** Helm `agent.operatorCredential.{secretName, key, type}` mounts one
  key of an existing Secret as a read-only file, `/var/run/secrets/hwflow/agent/<key>` (`defaultMode: 0400`; with
  the pod's `fsGroup` the kubelet makes it 0440, group 10001), never as env, values or a ConfigMap. The worker
  reads it at every resolve, so a rotated Secret applies without a restart; a missing or malformed file is a clear
  `CredentialUnavailable` (the run waits for a key like a missing one) unless the tenant has its own credential.
  Only the listed tenants get it; `agent.credentialOrder` (`HWPLATFORM_CREDENTIAL_ORDER`) is `operator,db` (the
  default: the mounted credential, else the tenant's own) or `db,operator`. Every other tenant gets only its own
  stored API key. The Secret must be in the release namespace; an existing one in namespace `hwflow` (made with
  `kubectl -n hwflow create secret generic hwflow-claude --from-literal=token=<claude setup-token>`) is copied with
  `kubectl -n hwflow get secret hwflow-claude -o yaml | sed '/namespace:/d;/uid:/d;/resourceVersion:/d;/creationTimestamp:/d' | kubectl -n <release-ns> apply -f -`.
  A rejected operator credential is logged (type and last 4) for the operator to replace; no database row changes.
- **Web.** Settings has a credential-type chooser; the subscription option says it is for operator tenants only.

Tests: `tests/test_credentials.py` (both adapters through one contract with fake validators, the gate on and off
at PUT and at resolve, the operator file: rotation, missing file, wrong tenant, order; the one-credential
environment; the runner's limit detection; redaction of both formats; the port against the database and the API),
`tests/test_workflow_design.py` (a subscription window's 4 h wait under time-skipping, and `retry` ending a wait)
and `tests/test_design_e2e.py` (an operator tenant's token through the API, the gateway and the real activities:
the session gets only `CLAUDE_CODE_OAUTH_TOKEN`, the limit becomes a 3 h timer, and the token is in no history,
event, log line, S3 object or API answer).

### Agent sandbox: threat model

The asset to protect is everything the session can reach that is not its own project: other tenants' data, the
platform's credentials (database, S3, master key, Temporal), the tenant's API key, and the cluster network. The
threat is the content the session reads: an uploaded design, a datasheet from `fetch_url`, a web search result or a
tool's output can carry instructions (prompt injection). The model can follow them and misuse its tools. So the
sandbox treats the session as untrusted code and does not rely on the model behaving.

| Misuse | Mitigation |
|---|---|
| Run commands, read the worker's files or environment | No shell: the session's built-in tools are only Read, Write, Edit, Glob, Grep, WebSearch (`tools=`); Bash, WebFetch, NotebookEdit, Task/Agent, Skill and the rest are absent and also in `disallowed_tools`. A PreToolUse hook (its deny is final) and `can_use_tool` reject any path that leaves the workspace after `realpath` (absolute paths, `..`, `~`, symlinks, glob roots) and writes to the read-only `.hwflow-ref/` (`hwplatform.agent.sandbox.PathGuard`). |
| Steal the platform's credentials | The CLI starts through a launcher that keeps an allowlist of variables (PATH, HOME, the tenant key, Claude Code switches) and drops the rest (the SDK merges the parent's environment otherwise). The worker process is non-dumpable (`PR_SET_DUMPABLE=0`), so a same-uid child cannot read `/proc/<worker>/environ`. The agent pod carries only the app DB role (row-level security), S3, the master key and Temporal; not the JWT secret, the owner or relay DB roles or NATS. |
| Load hooks, MCP servers or settings from the upload | `setting_sources=[]` and `strict_mcp_config`; `.claude/` and `.mcp.json` are removed from the workspace when it is materialized and never saved. |
| Exfiltrate data or reach internal services over the network | The agent pod's NetworkPolicy allows DNS, Postgres, Temporal and SeaweedFS, and HTTPS (443) to public addresses only (`networkPolicy.agentEgressCidrs` pins it to Anthropic's ranges or a proxy). WebSearch runs on Anthropic's side. `fetch_url` runs on the engine queue and refuses non-http(s), ports other than 80/443, credentials in the URL and any host with a private, loopback, link-local or reserved address; it checks the connected peer and each redirect (at most 5) and caps the body at 20 MiB. |
| Reach another tenant | The workspace, transcript and versions are the run's tenant's S3 prefix and RLS rows; every tool call carries the run's `RunCtx`, never one from the model. |
| Abuse the engine through arguments | Engine tools build their `hwflow` command lines from validated fields (`--` before free text, `--opt=value`), never a shell string. |
| Leak the tenant's key or token | It is resolved inside the activity and goes only into the subprocess environment: not in workflow inputs, heartbeats, errors, events, logs (`logredact`), transcripts or artifacts. `tests/test_design_e2e.py` checks the histories, the outbox, the captured logs and every S3 object of the run's tenant for it, for an API key and for a subscription token. |
| Run up cost | `max_turns` per stage (the agents' `maxTurns`), optional `HWPLATFORM_AGENT_MAX_BUDGET_USD`, usage recorded per model, and the run's 2 h activity timeout. |
| Escape the container | uid 10001, read-only root, all capabilities dropped, no privilege escalation, seccomp RuntimeDefault, no service-account token, the workspace on an emptyDir. |

Residual risks: the session can write anything inside its own project (a design version records every change and
the person reviews it); it can put text it read into a question to the product person or into the design files;
WebSearch results are untrusted text. The model is told that fetched text is data, but that is a mitigation, not
a boundary.

Tests: `tests/test_workflow_design.py` (the workflow in the Temporal test servers with a fake `agent_stage`: stage
order, question and answer, key wait and `retry`, rate-limit timer, worker restart resume, cancellation, and the
`build` tool starting a linked BuildWorkflow through the real backend), `tests/test_agent_sandbox.py` (the path
guard, the launcher's environment, the SDK options), `tests/test_agent_tools.py` (the MCP tools, engine argument
building, the URL check, workspace snapshots, the stage prompts) and `tests/test_design_e2e.py` (the API, the
real gateway and activities with a scripted runner, and the key-leak checks).

The real Claude API smoke (`tests/test_agent_smoke.py`, marker `anthropic_smoke`: one short Haiku session through
the sandbox with an MCP tool call, a few cents on an API key) runs only on request and with a credential in the
environment; the default suite and CI never collect it. It prints the turns, tokens and duration:

```
ANTHROPIC_API_KEY=sk-ant-api... uv run --package hwplatform --extra agent pytest -q -s -m anthropic_smoke tests/test_agent_smoke.py
# or, the operator's own subscription token, with ANTHROPIC_API_KEY unset:
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat... uv run --package hwplatform --extra agent pytest -q -s -m anthropic_smoke tests/test_agent_smoke.py
```

## Swapping for OIDC (Keycloak, M2)

The API depends only on `AuthBackend.authenticate(bearer) -> Principal(user_id, tenant_id)`. An `OidcAuthBackend` validates the IdP's JWT (JWKS, issuer, audience), maps `sub` to a `users` row (create on first sight, `password_hash` NULL) and a tenant claim (or a tenant header checked against memberships) to `tenant_id`, and sets `issues_tokens = False`. Pass it as `create_app(auth=OidcAuthBackend(...))`: the signup, login, refresh and logout routes are then not mounted, `/auth/me` stays, and everything below the `TenantContext` (membership check, repositories, RLS) is unchanged.

## Open points

- No e-mail delivery: the dev sender logs to stdout. A real `EmailSender` (SMTP or provider API) is needed before sign-up is exposed.
- Password reset and e-mail change are not implemented.
- Invitation creation is not rate limited beyond the admin role requirement.
- After logout an access token stays valid until it expires (15 min, see Hardening). A removed member is refused at once.
- The OIDC mode does not mount the sign-up, verify and accept-invite routes; mapping invitations to IdP users is an M2 task.
- Design agent (#15): the firmware stage cannot run its host tests (`make test`, gcc), since the session has no
  shell; it writes the code and uses the `fw` tool. `simulate_answer` (the evals answerer) is #19. There is no
  `approve` tool: the sign-off stays with the person.
