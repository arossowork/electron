# Platform on Kubernetes (Helm chart)

Chart: `deploy/helm/hwflow-platform` (issue #17). One chart, two profiles: `values-reduced.yaml` for the single-node k3s of the VPS (2 vCPU, 4 GB, shared with other apps) and `values-full.yaml` for the M2 split (placeholders, see ADR 0001 section 8). The deploy to the VPS is a separate step; this document is what you follow for it.

## What it installs

| Component | Kind | Notes |
|---|---|---|
| `api` | Deployment | `hwplatform-api`, the outbox relay runs inside it (leader-elected by a Postgres advisory lock). Image `deploy/images/api.Dockerfile` |
| `web` | Deployment | static SPA image (`web/Dockerfile`), `web.enabled` |
| `worker` | Deployment | one process polling all queues (reduced) or one Deployment per queue (`worker.perQueue`, full); `worker.command` and `worker.queuesEnvVar` are placeholders until #9; image `deploy/images/worker.Dockerfile` (#10), see Worker image |
| `postgres` | StatefulSet | PG 17, init ConfigMap creates the same roles and databases as `deploy/compose/postgres-init.sh` (`hwflow`, `hwflow_app`, `hwflow_relay`, `temporal`; databases `hwflow`, `temporal`, `temporal_visibility`) |
| `temporal` | Deployment | single process, PG persistence and visibility. The schema step is an init container (idempotent, runs before the server) |
| `temporal-ui` | Deployment | `temporal.ui.enabled`, off in reduced |
| `seaweedfs` | StatefulSet | single node, S3 on 8333 |
| `bucket` | hook Job | post-install/post-upgrade: creates `hwflow-artifacts` (and `hwflow-backups` for an in-cluster target) |
| `nats` | StatefulSet | JetStream, file storage on a PVC |
| `migrate` | hook Job | `hwplatform-migrate` (Alembic, JetStream stream, bucket CORS rule for `HWPLATFORM_WEB_ORIGINS`, Temporal search attributes), **post-install and pre-upgrade**, hook weight 5 (after `bucket`). An init container creates the Temporal namespace (`default`, 72h retention) first. There is no separate search-attribute Job |
| `purge` | CronJob | `hwplatform-purge`, nightly |
| `backup` | CronJob | `pg_dump` of the three databases and the roles, uploaded to the object store, retention in days |

Deviations from "pre-install" for the migration: Postgres is part of this release, so at `pre-install` it does not exist yet. The migration Job is therefore a `post-install` hook (the API is already up; its relay logs "relation outbox does not exist" for a few seconds, then recovers) and a `pre-upgrade` hook on upgrades, so a new API version never runs against an old schema. All hooks wait for their dependencies, so they work with and without `helm --wait`.

## Install

Prerequisites: the `apps-high` and `apps-low` PriorityClasses (`kubectl apply -f docker/k8s/cluster.yaml`, already on the VPS), the images in the node's containerd, a default StorageClass (k3s: `local-path`).

```
# 1. images (k3s imports them from a tarball, like docker/k8s/deploy.sh does for hwflow)
docker build -f deploy/images/api.Dockerfile -t hwplatform-api:0.1.0 .
docker save hwplatform-api:0.1.0 | sudo k3s ctr images import -
# (web: web/Dockerfile; worker: deploy/images/worker.Dockerfile. Until they exist: --set web.enabled=false --set worker.enabled=false)

# 2. secrets (below), then
helm upgrade --install hwp deploy/helm/hwflow-platform -n hwflow-platform --create-namespace \
  -f deploy/helm/hwflow-platform/values-reduced.yaml \
  --set images.api.tag=0.1.0 --set global.imagePullPolicy=Never \
  --set global.publicUrl=https://<public host>/platform \
  --wait --timeout 10m
```

Use release name `hwp` and namespace `hwflow-platform` to match `deploy/helm/nginx-platform.conf` (Services are named `<release>-api`, `<release>-web`, ...). Third-party images (Postgres, Temporal, SeaweedFS, NATS, aws-cli) are pulled from their registries; the pins are in `values.yaml` and match `deploy/compose/platform.yaml`.

## Secrets

Nothing secret is in a values file. The chart reads one existing Secret (`secrets.name`, default `hwflow-platform-secrets`) with these keys:

| Key | Used by |
|---|---|
| `postgres-password` | Postgres superuser (init, backups) |
| `hwflow-db-password` | role `hwflow`, schema owner (migrate, purge) |
| `hwflow-app-db-password` | role `hwflow_app` (API, workers; row-level security applies) |
| `hwflow-relay-db-password` | role `hwflow_relay` (outbox relay) |
| `temporal-db-password` | role `temporal` |
| `jwt-secret` | API token signing, at least 32 bytes |
| `master-key` | base64 32-byte key encrypting the tenants' LLM keys. Back it up outside the cluster: losing it loses every stored key |
| `master-keys` (optional) | keyring `<version>:<base64>,...` for rotation (`hwplatform-rotate-master`) |
| `s3-access-key`, `s3-secret-key` | SeaweedFS identity and API/worker access |

The passwords end up in database URLs, so use hex (no URL-special characters). To get the commands printed (nothing is applied by the script):

```
deploy/helm/gen-secrets.sh hwflow-platform > /tmp/create-secrets.sh   # review it
sh /tmp/create-secrets.sh
```

which expands to `kubectl -n hwflow-platform create secret generic hwflow-platform-secrets --from-literal=postgres-password=... ` with fresh `openssl rand` values. The database roles take their passwords from the Secret only on the first start of an empty volume: changing a password later also needs `ALTER ROLE`.

## Security

- All pods run non-root with `seccompProfile: RuntimeDefault`, no privilege escalation, all capabilities dropped, a read-only root filesystem (writable `emptyDir` for `/tmp` and data volumes), and CPU/memory requests and limits. Exception: the Temporal server image renders its config at start, so its root filesystem is writable.
- NetworkPolicies (`networkPolicy.enabled`): default deny ingress for the release; allowed from anywhere (host nginx, Traefik): `api` (8080), `web`, `temporal-ui`; Postgres, Temporal, SeaweedFS and NATS accept only the platform pods that use them. Verified on k3d with an unlabeled pod: it reaches the API and nothing else. Worker pods can additionally only egress to the release's pods, DNS and the internet outside `networkPolicy.workerEgressExceptCidrs` (private ranges).
- The kube-router policy controller needs a few seconds to admit a new pod, so connections made in the first seconds of a pod can be refused. Init containers and hooks therefore retry.
- No PodDisruptionBudget in reduced mode (one replica of everything).

## Exposing it: host nginx (VPS) or Ingress

The VPS has a host nginx that forwards to cluster Services (`docker/k8s/nginx-apps.conf`). Copy `deploy/helm/nginx-platform.conf` to `/etc/nginx/apps.d/hwplatform.conf` and `sudo nginx -s reload`. It maps `/platform/api/` to `hwp-api:8080` (SSE-safe: no buffering, 1 h read timeout, 64 MiB uploads), `/platform/` to `hwp-web:80`, and turns the shared basic-auth login off for these paths because the platform has its own accounts and rate limits. Set `global.publicUrl` to the externally visible URL.

For a cluster with Traefik, `--set ingress.enabled=true --set ingress.host=... --set ingress.tls.enabled=true --set ingress.tls.secretName=...` renders an Ingress: `/api` to the API, `/` to web.

## Upgrade

```
helm upgrade hwp deploy/helm/hwflow-platform -n hwflow-platform -f deploy/helm/hwflow-platform/values-reduced.yaml --set images.api.tag=<new> --wait
```

Order: the `migrate` Job runs first (pre-upgrade), then the Deployments roll (the API uses `RollingUpdate`; Temporal and workers `Recreate`). The migrate Job re-asserts the namespace, the bucket CORS rule and the search attributes; the bucket Job re-asserts the bucket. A failed migration stops the upgrade with the old pods still serving. Database schema changes are forward-only: roll back the image with `helm rollback` only when the migration was compatible, otherwise restore a backup.

## Backup and restore

`backup.schedule` (default 02:37 UTC): an init container dumps `hwflow`, `temporal`, `temporal_visibility` (custom format) and the roles (`globals.sql`) as the Postgres superuser (the tables use FORCE ROW LEVEL SECURITY, so even the owner role cannot dump them); a second container uploads to `s3://<backup.bucket>/postgres/<UTC timestamp>/` and deletes timestamps older than `backup.retentionDays`.

By default the target is the in-cluster SeaweedFS: it protects from mistakes and bad migrations, not from losing the disk. For disaster recovery set `backup.target.endpoint` (any S3) and `backup.target.secretName` (keys `access-key`, `secret-key`). The artifact bucket itself is **not** copied by the chart: SeaweedFS volumes live on a PVC, so copy the PVC directory (`/var/lib/rancher/k3s/storage/...`) or `aws s3 sync` the bucket to the same external target (open item for #17 on the VPS).

Run one now: `kubectl -n hwflow-platform create job --from=cronjob/hwp-backup backup-now`.

Restore (tested on k3d: dump of a database with a tenant, project, design version and outbox row restored into a new database, identical counts and Alembic revision):

```
NS=hwflow-platform
# 1. fetch (any machine with aws-cli and a port-forward of svc/hwp-seaweedfs 8333, or a one-off pod)
aws --endpoint-url http://127.0.0.1:8333 s3 ls s3://hwflow-backups/postgres/
aws --endpoint-url http://127.0.0.1:8333 s3 cp s3://hwflow-backups/postgres/<ts>/hwflow.dump .
# 2. scale the writers down
kubectl -n $NS scale deploy/hwp-api deploy/hwp-worker --replicas=0
# 3. restore into an empty database (drop + create the real one for a disaster restore; use hwflow_restored to inspect first)
kubectl -n $NS exec hwp-postgres-0 -- sh -c 'PGPASSWORD=$POSTGRES_PASSWORD psql -h 127.0.0.1 -U postgres -d postgres \
  -c "DROP DATABASE hwflow" -c "CREATE DATABASE hwflow OWNER hwflow"'
kubectl -n $NS exec -i hwp-postgres-0 -- sh -c 'PGPASSWORD=$POSTGRES_PASSWORD pg_restore -h 127.0.0.1 -U postgres -d hwflow --exit-on-error' < hwflow.dump
# 4. same for temporal / temporal_visibility (scale deploy/hwp-temporal to 0 first); on a new volume restore globals.sql first
kubectl -n $NS scale deploy/hwp-api --replicas=1; kubectl -n $NS scale deploy/hwp-worker --replicas=1
```

Restoring Temporal also needs the matching artifacts in the object store; restore the PVC or bucket from the same moment.

## Memory (measured)

Reduced profile, k3d on k3s v1.31 (the same k3s family as the VPS), after the smoke test and two idle minutes, `kubectl top pods` (working set):

| Pod | Working set | Request | Limit |
|---|---|---|---|
| hwp-api | 97 MiB | 160 MiB | 320 MiB |
| hwp-postgres-0 | 142 MiB | 256 MiB | 384 MiB |
| hwp-temporal | 85 MiB | 256 MiB | 512 MiB |
| hwp-seaweedfs-0 | 31 MiB | 192 MiB | 384 MiB |
| hwp-nats-0 | 7 MiB | 32 MiB | 128 MiB |
| **Total** | **362 MiB** | **896 MiB** | |

Web adds a request of 16 MiB and the worker 384 MiB (limit 1.5 GiB while a build runs), so the requests at rest are about 1.3 GiB, under the 1.5 GiB target. Postgres grows with its page cache and the worker is the one that spikes; none of this includes the other apps on the node.

## Worker image

`deploy/images/worker.Dockerfile` (issue #10): multi-stage, Ubuntu 24.04 like the all-in-one image because KiCad 10 comes from its PPA. The builder stage runs `uv sync --frozen --no-dev --package hwplatform` (hwflow and hwplatform non-editable, bytecode compiled), clones KiCadRoutingTools at the pinned commit and builds its Rust router from source (the upstream release asset moved past the pinned commit and its ABI no longer matches, so the pinned commit is built with a rustup toolchain that stays in the builder). The runtime stage has `kicad-cli` with the symbol and footprint libraries (the GUI programs, demos, templates, help, translations and the Mesa LLVM driver are deleted in the same layer; no 3D models), ngspice, tini, the venv with its Python, KRT, `blocks/` and `library/` (under `HWFLOW_ROOT=/opt/hwflow`, until the engine packages them), and Java + Freerouting. No Claude CLI, no UI: the agent worker (#15) gets its own image.

```
docker build -f deploy/images/worker.Dockerfile -t hwplatform-worker:dev .
docker run --rm --read-only --tmpfs /tmp --tmpfs /scratch:uid=10001,gid=10001,size=1g,exec \
  -v "$PWD/examples:/examples:ro" -v "$PWD/deploy/images/worker-smoke.sh:/smoke.sh:ro" hwplatform-worker:dev /smoke.sh layout
```

Build arguments: `WITH_FREEROUTING` (default 1; Freerouting runs only when a design sets `board.router: freerouting` or KRT is missing, so a cluster that never does can build with 0 and drop about 230 MB: JRE 25 and the jar; a design that asks for it then fails with `ToolMissing`), `WITH_RENDER` (default 0; adds xvfb and Mesa, about 250 MB, so `kicad-cli pcb render` in the fab stage can write `pcb_3d_top.png`; without it that step reports `FAILED` in the stage messages and the fab stage carries on. The product decision is whether the fab package must contain the bare-board PNG), `KRT_COMMIT`, `FREEROUTING_VERSION`.

Runtime contract: user `10001:10001`, `tini` as PID 1, entrypoint `worker-entrypoint.sh` (creates the directories below, then runs the command), default `CMD ["hwplatform-worker"]` (override the command to run engine commands). The root file system is read-only: `HOME`, `XDG_*`, `KICAD_CONFIG_HOME`, `TMPDIR`, `HWFLOW_CAS_DIR` and the Java temp and home directories all point under `/scratch` (the pod's emptyDir, `worker.scratchSize`). Two things write outside it, so the chart mounts emptyDirs there: `/tmp` (ngspice calls glibc `tmpfile()`, which ignores `TMPDIR` and fails with `Read-only file system` otherwise) and `/dev/shm`. Tool paths are environment variables set in the image (`KICAD_CLI`, `KICAD_SHARE`, `JAVA`, `FREEROUTING`, `KRT_DIR`, `HWFLOW_ROOT`, `HWFLOW_TOOLS_DIR`); the engine runs `kicad-cli` with `LANG=en_US.UTF-8`, so the image generates that locale.

Measured (x86_64, Docker 29, `examples/sensor_preamp`, container limited like the reduced worker: 2 CPUs, 1536 MiB, read-only root, 1 GiB scratch tmpfs):

| | all-in-one `docker/Dockerfile` | worker image |
|---|---|---|
| image size (`docker images`) | 3.79 GB | 2.88 GB (filesystem about 2.1 GB) |
| layers | | apt (KiCad libraries, ngspice, JRE, locale) 1.41 GB; venv 484 MB; KRT + Freerouting jar 146 MB; Python 97 MB; base 88 MB |
| `hwflow lib show Device:R` | | 1 s |
| `hwflow check` | | 5 s |
| `hwflow build --until sim` (check, sch with ERC, sim) | | 5 s after check (all 7 requirements verified) |
| `hwflow build --until layout` (area search, KRT, repair, DRC) | | 141 s, 0 unrouted, DRC 0 errors |
| peak memory during the layout build (`docker stats`, sampled every 2 s) | | 310 MiB for the engine process tree |

The 310 MiB is the engine alone. `hwplatform-worker` adds the Temporal client and the asyncpg/boto3 stack (about 100 to 150 MB, estimated, not yet measured: #9 has not landed), so about 450 to 500 MiB for one stage at a time (slots: 1 for layout, sim and verify), which fits the reduced limit of 1536 MiB with room for a bigger board; request 384 Mi, unchanged. Re-measure with `kubectl top` once `hwplatform-worker` runs. Scratch used by the example: a few MB; 1 GiB (reduced) leaves room for the stage copies, routecache and CAS.

Where the size goes: KiCad 10's package pulls GTK, wx, WebKitGTK (eeschema's kiface links it, so it cannot be removed) and OpenCascade; that is the floor for `kicad-cli` from the PPA, about 1.1 GB of libraries.

Probes. There is no `HEALTHCHECK` in the image: Kubernetes probes are the pod's job. `worker.probes.enabled` (default false) adds startup, readiness and liveness HTTP probes on a `health` port. Issue #9 must expose, in the `hwplatform-worker` process, an HTTP server on `HWPLATFORM_WORKER_HEALTH_PORT` (the chart sets it from `worker.probes.port`, default 8081) with `GET /readyz` (200 once the Temporal client is connected and all pollers run; 503 while starting or when a poller died) and `GET /livez` (200 while the asyncio loop answers; must not depend on Temporal or Postgres, or an outage restarts every worker). A stage in a child process must not make `/livez` fail (heartbeats cover stuck stages). When it lands, set `worker.probes.enabled: true` in the values files. Also needed from #9: `--queues` and `HWPLATFORM_WORKER_QUEUES` (the chart's `worker.queuesEnvVar`), SIGTERM handling inside `terminationGracePeriodSeconds: 120` (stop polling, finish or heartbeat-cancel the stage).

Priority: workers use `worker.priorityClassName` (`apps-high` in reduced, as the platform core, so a long build is evicted last; `apps-low` lets the interactive apps win), see Coexisting with the other apps on the VPS.

CI: `.github/workflows/worker-image.yml` builds the image with the buildx GitHub cache and runs `worker-smoke.sh sim` in it (read-only root, non-root) on changes to `docker/`, `deploy/images/`, `src/hwflow/`, `services/platform/`, `blocks/`, `library/`, `uv.lock`, `pyproject.toml`.

## Observability (issue #18)

Always on, no extra pods: the API, the relay and the worker log one JSON object per line to stdout (`kubectl logs | jq`), and serve Prometheus metrics (`/metrics` on the API port 8080 and on the worker health port 8081). The procedures are in `docs/runbook.md`.

Optional, `--set observability.enabled=true` (off in both profiles): a VictoriaMetrics single-node StatefulSet (`<release>-victoriametrics`, 1 GiB PVC, 7 days) that scrapes the API and every worker pod through two headless Services, and a Grafana Deployment with the data source and the dashboard `hwflow platform` (`dashboards/hwflow-platform.json`) provisioned from ConfigMaps. Grafana has no Ingress: `kubectl -n hwflow-platform port-forward svc/hwp-grafana 3000:3000`. `observability.grafana.enabled=false` keeps only the store (query it with `port-forward svc/hwp-victoriametrics 8428`); `observability.temporalSdkMetrics=true` also exposes the Temporal SDK metrics of the worker on port 9464 and scrapes them. NetworkPolicies are added for the scrape (VictoriaMetrics to the worker ports) and the queries (Grafana to VictoriaMetrics). The Grafana admin password is the optional Secret key `grafana-admin-password`.

Measured on k3d (k3s v1.31, reduced profile without web and worker, observability on, two idle minutes after the first scrapes, `kubectl top`): VictoriaMetrics 26 MiB, Grafana 103 MiB, so 129 MiB together (requests 64 + 96 = 160 MiB, limits 160 + 256 MiB). The rest of the stack in the same run: api 99, postgres 71, temporal 82, seaweedfs 32, nats 9 MiB. Do not enable it on the shared VPS unless there are 200 MiB to spare; the logs and `/metrics` need none of it.

## Coexisting with the other apps on the VPS

- Own namespace (`hwflow-platform`), own Services; the existing `hwflow` UI and `singing` apps are untouched. The only shared pieces are the PriorityClasses and the host nginx (one more file in `apps.d/`).
- Platform core and workers use `apps-high` (`global.priorityClassName`, `worker.priorityClassName`): under memory pressure the kubelet evicts pods above their requests, lowest priority first, so `apps-low` apps go first. The requests are deliberately above the idle use, so the platform is rarely the one above its request. Set `worker.priorityClassName: apps-low` to let the interactive apps win over a long build.
- k3s ships Traefik, which this setup does not use (the host nginx does); the chart's Ingress is off by default.
- Disk: PVCs are 2 + 2 + 0.5 GiB in reduced (`local-path`) plus 1 GiB worker scratch, 5.5 GiB in all, to fit a VPS with about 8 GB free. Raise `postgres.persistence.size` and `seaweedfs.persistence.size` when there is room, and prune images first: `sudo k3s crictl rmi --prune`, and cap container logs in `/etc/rancher/k3s/config.yaml` (`kubelet-arg: ["container-log-max-size=10Mi", "container-log-max-files=3"]`).

## Local check

```
helm lint deploy/helm/hwflow-platform -f deploy/helm/hwflow-platform/values-reduced.yaml
helm template hwp deploy/helm/hwflow-platform -f deploy/helm/hwflow-platform/values-full.yaml | kubeconform -strict -ignore-missing-schemas
k3d cluster create hwflow-test --servers 1 --agents 0   # then follow Install with --set web.enabled=false --set worker.enabled=false
```

`.github/workflows/helm.yml` runs lint, `helm template` and kubeconform for both profiles and an install smoke test on k3d.

## Settings added for the web UI and Temporal (issue #17, aligned with #39)

- `global.webOrigins` (default: the origin of `global.publicUrl`) becomes `HWPLATFORM_WEB_ORIGINS` for api, worker and the migrate Job; `hwplatform-migrate` writes the bucket CORS rule from it (the SeaweedFS identity has Admin, so PutBucketCors is allowed).
- `seaweedfs.publicUrl` (optional) becomes `HWPLATFORM_S3_PUBLIC_URL`; the web container gets `CSP_CONNECT_EXTRA` from `web.cspConnectExtra` (default `seaweedfs.publicUrl`) and `API_UPSTREAM` pointing to the api Service.
- `temporal.sqlMaxConns` (4) and `temporal.sqlMaxIdleConns` (2) set `SQL_MAX_CONNS` / `SQL_MAX_IDLE_CONNS`, the Postgres pool of each Temporal service. Temporal holds at most 4 services x 2 stores x 4 = 32 connections, instead of 45 of 60 at the default of 20, so Temporal, api (pool 5 + relay), migrate and backup fit `max_connections=60` with headroom. The compose stack uses the same values.
