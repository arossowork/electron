# Platform dev environment (reduced stack)

Local stack for the hosted hwflow platform (epic #21, issue #8). It is independent of `docker/compose.yaml` (the hwflow UI/CLI container).

## What runs

| Service | Image (pinned) | Host port (127.0.0.1) | Role |
|---|---|---|---|
| postgres | `postgres:17.11-alpine` | 15432 (`PG_PORT`) | databases `hwflow`, `temporal`, `temporal_visibility`; roles `hwflow` (owner), `hwflow_app` (hwplatform API, non-owner, see `services/platform/README.md`), `hwflow_relay` (outbox relay: reads all tenants' events, nothing else), `temporal` |
| temporal-schema (one-shot) | `temporalio/admin-tools:1.31.4` | - | creates/updates the Temporal schemas (`temporal-sql-tool`) |
| temporal | `temporalio/server:1.31.4` | 7233 (`TEMPORAL_PORT`) | single process, Postgres persistence and visibility, no Elasticsearch, 4 history shards |
| temporal-namespace (one-shot) | `temporalio/admin-tools:1.31.4` | - | creates namespace `default` (72h retention) if missing |
| temporal-ui | `temporalio/ui:2.57.0` | 8233 (`TEMPORAL_UI_PORT`) | web UI |
| seaweedfs | `chrislusf/seaweedfs:4.48` | 8333 (`S3_PORT`) | `weed server -s3`, single node, S3 keys from env |
| seaweedfs-bucket (one-shot) | `amazon/aws-cli:2.34.0` | - | creates bucket `hwflow-artifacts` if missing |
| nats | `nats:2.14.8-alpine` | 4222, 8222 (`NATS_PORT`, `NATS_MON_PORT`) | JetStream, file storage in a volume |

Temporal 1.31 has no `auto-setup` image: schema setup and the namespace are separate one-shot jobs, so `up` is idempotent. The server image has no `temporal` CLI; its healthcheck is a TCP check, the namespace job proves the API answers.

Postgres is tuned small: `shared_buffers=96MB`, `max_connections=60`. Memory limits (`mem_limit`) total 2.0 GB for the long-running services; the one-shot jobs exit.

## Start

```
cp deploy/compose/.env.example deploy/compose/.env   # edit the secrets; .env is git-ignored
scripts/platform.sh up        # waits until all services are healthy
scripts/platform.sh status
scripts/platform.sh mem       # docker stats --no-stream per container
scripts/platform.sh down      # keep the volumes
scripts/platform.sh down -v   # also delete them
```

Endpoints: `postgresql://hwflow:<HWFLOW_DB_PASSWORD>@127.0.0.1:15432/hwflow`, Temporal `127.0.0.1:7233`, UI http://127.0.0.1:8233, S3 `http://127.0.0.1:8333` (path style, region any, bucket `hwflow-artifacts`), NATS `nats://127.0.0.1:4222`.

`HWFLOW_RELAY_DB_PASSWORD` (role `hwflow_relay`, outbox relay) is new: add it to an existing `.env`, and recreate the Postgres volume (`down -v`) or create the role by hand, because `postgres-init.sh` runs only on an empty volume. The `hwplatform` integration tests use Postgres, SeaweedFS and NATS from this stack (`HWPLATFORM_TEST_*` variables, see `services/platform/README.md`); several dev stacks can coexist with `COMPOSE_PROJECT_NAME` and different `*_PORT` values.

Ports are bound to localhost only. 5432 is not the default because it is often taken on a dev host.

## Measured idle memory

Fresh volumes, 2 minutes after `up` healthy, `docker stats --no-stream`, Docker 29.8 on Linux:

| Container | RSS |
|---|---|
| postgres | 105.7 MiB |
| temporal | 86.5 MiB |
| seaweedfs | 33.4 MiB |
| temporal-ui | 7.1 MiB |
| nats | 5.6 MiB |
| **Total** | **238 MiB** |

Far below the 1.5-2 GB target; the target leaves room for the application services (API, workers) and load. These numbers are at rest with no workflows or objects; Temporal and Postgres grow with load.

## Verified

- `psql` connects to the three databases; Temporal schemas are loaded (40 and 3 tables).
- `temporal operator namespace list` shows `default`; a second `up` leaves it unchanged.
- S3 put, get and delete on `hwflow-artifacts` with aws-cli.
- NATS: `/healthz?js-enabled-only=true` is `ok`; `nats stream add/ls/rm` works.

## Web UI and API (profile `web`)

`COMPOSE_PROFILES=web scripts/platform.sh up` also starts the platform API (`api`), a one-shot that registers the Temporal search attributes, and the web UI (`web`, http://127.0.0.1:8088, `WEB_PORT`). Details, required `.env` values and the e2e test: [docs/web.md](web.md). The old local UI `ui/server.py` is deprecated and will be removed once the platform runs the agent (#15).
