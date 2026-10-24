# Platform on Kubernetes (Helm chart)

Chart: `deploy/helm/hwflow-platform` (issue #17). One chart, two profiles: `values-reduced.yaml` for the single-node k3s of the VPS (2 vCPU, 4 GB, shared with other apps) and `values-full.yaml` for the M2 split (placeholders, see ADR 0001 section 8). The deploy to the VPS is a separate step; this document is what you follow for it.

## What it installs

| Component | Kind | Notes |
|---|---|---|
| `api` | Deployment | `hwplatform-api`, the outbox relay runs inside it (leader-elected by a Postgres advisory lock). Image `deploy/images/api.Dockerfile` |
| `web` | Deployment | static SPA image (`web/Dockerfile`), `web.enabled` |
| `worker` | Deployment | one process polling all queues (reduced) or one Deployment per queue (`worker.perQueue`, full); `worker.command` and `worker.queuesEnvVar` are placeholders until #9; image from #10 |
| `postgres` | StatefulSet | PG 17, init ConfigMap creates the same roles and databases as `deploy/compose/postgres-init.sh` (`hwflow`, `hwflow_app`, `hwflow_relay`, `temporal`; databases `hwflow`, `temporal`, `temporal_visibility`) |
| `temporal` | Deployment | single process, PG persistence and visibility. The schema step is an init container (idempotent, runs before the server) |
| `temporal-setup` | hook Job | post-install/post-upgrade: namespace `default` (72h retention) and the Keyword search attributes `HwTenantId HwProjectId HwRunId HwRunKind` |
| `temporal-ui` | Deployment | `temporal.ui.enabled`, off in reduced |
| `seaweedfs` | StatefulSet | single node, S3 on 8333 |
| `bucket` | hook Job | post-install/post-upgrade: creates `hwflow-artifacts` (and `hwflow-backups` for an in-cluster target) |
| `nats` | StatefulSet | JetStream, file storage on a PVC |
| `migrate` | hook Job | `hwplatform-migrate` (Alembic + JetStream stream), **post-install and pre-upgrade** |
| `purge` | CronJob | `hwplatform-purge`, nightly |
| `backup` | CronJob | `pg_dump` of the three databases and the roles, uploaded to the object store, retention in days |

Deviations from "pre-install" for the migration: Postgres is part of this release, so at `pre-install` it does not exist yet. The migration Job is therefore a `post-install` hook (the API is already up; its relay logs "relation outbox does not exist" for a few seconds, then recovers) and a `pre-upgrade` hook on upgrades, so a new API version never runs against an old schema. All hooks wait for their dependencies, so they work with and without `helm --wait`.

## Install

Prerequisites: the `apps-high` and `apps-low` PriorityClasses (`kubectl apply -f docker/k8s/cluster.yaml`, already on the VPS), the images in the node's containerd, a default StorageClass (k3s: `local-path`).

```
# 1. images (k3s imports them from a tarball, like docker/k8s/deploy.sh does for hwflow)
docker build -f deploy/images/api.Dockerfile -t hwplatform-api:0.1.0 .
docker save hwplatform-api:0.1.0 | sudo k3s ctr images import -
# (web: web/Dockerfile; worker: docker/Dockerfile via #10. Until they exist: --set web.enabled=false --set worker.enabled=false)

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

Order: the `migrate` Job runs first (pre-upgrade), then the Deployments roll (the API uses `RollingUpdate`; Temporal and workers `Recreate`). The post-upgrade hooks re-assert the bucket, namespace and search attributes. A failed migration stops the upgrade with the old pods still serving. Database schema changes are forward-only: roll back the image with `helm rollback` only when the migration was compatible, otherwise restore a backup.

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

## Coexisting with the other apps on the VPS

- Own namespace (`hwflow-platform`), own Services; the existing `hwflow` UI and `singing` apps are untouched. The only shared pieces are the PriorityClasses and the host nginx (one more file in `apps.d/`).
- Platform core and workers use `apps-high` (`global.priorityClassName`, `worker.priorityClassName`): under memory pressure the kubelet evicts pods above their requests, lowest priority first, so `apps-low` apps go first. The requests are deliberately above the idle use, so the platform is rarely the one above its request. Set `worker.priorityClassName: apps-low` to let the interactive apps win over a long build.
- k3s ships Traefik, which this setup does not use (the host nginx does); the chart's Ingress is off by default.
- Disk: PVCs are 8 + 10 + 1 GiB in reduced (`local-path`). The VPS has about 8 GB free, so lower `postgres.persistence.size` and `seaweedfs.persistence.size` or prune images first (the issue's disk item): `sudo k3s crictl rmi --prune`, and cap container logs in `/etc/rancher/k3s/config.yaml` (`kubelet-arg: ["container-log-max-size=10Mi", "container-log-max-files=3"]`).

## Local check

```
helm lint deploy/helm/hwflow-platform -f deploy/helm/hwflow-platform/values-reduced.yaml
helm template hwp deploy/helm/hwflow-platform -f deploy/helm/hwflow-platform/values-full.yaml | kubeconform -strict -ignore-missing-schemas
k3d cluster create hwflow-test --servers 1 --agents 0   # then follow Install with --set web.enabled=false --set worker.enabled=false
```

`.github/workflows/helm.yml` runs lint, `helm template` and kubeconform for both profiles and an install smoke test on k3d.
