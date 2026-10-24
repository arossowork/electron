# Runbook: what to look at when a run misbehaves

Light observability (issue #18). M1 needs no log or trace backend: every process writes **one JSON object per line to
stdout**, `kubectl logs` and `jq` are the query tool, `/metrics` the dashboard. VictoriaMetrics and Grafana are an
optional extra (below). Commands assume namespace `hwflow-platform` and release `hwp` (the chart defaults); in the compose dev stack use
`docker compose logs` instead of `kubectl logs`.

## Log lines

```
{"ts":"2026-10-24T07:12:34.748Z","level":"error","logger":"hwplatform.activity","msg":"activity failed: ...",
 "service":"worker","tenant_id":"...","run_id":"...","workflow_id":"build-...","activity":"run_stage_activity",
 "stage":"sim","attempt":2,"error_class":"ToolMissing"}
```

| Field | Where it is set |
|---|---|
| `ts`, `level`, `logger`, `msg`, `service` (`api`, `relay`, `worker`, `stage-child`) | every line |
| `request_id` | API: the `X-Request-ID` the client sent (a token of `[A-Za-z0-9._:-]`, up to 128 characters), else generated; always echoed in the response header |
| `tenant_id`, `user_id` | API: after the token is checked; worker and stage child: from the run |
| `run_id`, `workflow_id`, `activity`, `stage`, `attempt` | worker: bound around every activity attempt; the stage child process gets them in its job file; API: `run_id` on the access line of `/runs/{run_id}` routes |
| `error_class` | a failed activity attempt (`ToolMissing`, `WorkerLost`, ... or the Python class), an unhandled API error, any `logger.exception` |
| `trace_id`, `span_id` | only when an OpenTelemetry span is active (`HWPLATFORM_OTEL_ENABLED=true` and an SDK configured); none in M1 |
| `route`, `path`, `method`, `status`, `duration_ms` | API access line (`logger: hwplatform.access`, `msg: request`); `route` is the template, `path` has no query string |

Keys shaped like `sk-ant-...` are replaced by `sk-ant-***` in `msg`, arguments, exceptions, extra fields and context values.
Settings: `HWPLATFORM_LOG_LEVEL` (INFO), `HWPLATFORM_LOG_FORMAT` (`json` | `text` for a terminal).

## Follow a failed run

Start from the run id in the UI (the run page URL, or `id` in `GET /api/v1/runs/{id}`).

1. **The run record.** `GET /api/v1/runs/{id}` gives `status`, `error`, `workflow_id` and the stage rows (`stages`: which stage failed, its `reason`). The UI shows the same. If the stage is `skipped` with `hard_failure:<stage>`, the cause is in that earlier stage.
2. **API logs.** What the user did and what the API answered (the `request_id` is in the response header and in the browser network tab):
   ```
   kubectl -n hwflow-platform logs deploy/hwp-api --since=2h | jq -c 'select(.run_id=="<run_id>" or .request_id=="<request_id>")'
   ```
   A start that failed before the workflow existed (`GatewayError`, quota 429, 503 without a master key) ends here.
3. **Temporal UI.** `kubectl -n hwflow-platform port-forward svc/hwp-temporal-ui 8080:8080` (needs `--set temporal.ui.enabled=true`; the compose stack has it on :8233). Open the workflow `workflow_id` of the run, or search `HwRunId = "<run_id>"` (also `HwTenantId`, `HwProjectId`, `HwRunKind`). The history shows each activity, its attempts, the retry timers and the failure with the error `type`. A workflow with a pending activity and no worker shows `ScheduleToStart` timing out: the worker is down or not polling that queue (step 4 and "Stuck run" below).
4. **Worker logs.** Everything an activity attempt did, including the stage child's output:
   ```
   kubectl -n hwflow-platform logs deploy/hwp-worker --since=2h | jq -c 'select(.run_id=="<run_id>")'
   kubectl -n hwflow-platform logs deploy/hwp-worker --since=2h | jq -r 'select(.run_id=="<run_id>" and .level=="error") | "\(.ts) \(.stage) attempt=\(.attempt) \(.error_class): \(.msg)"'
   ```
   With one Deployment per queue (full profile): `kubectl logs -l hwflow.io/role=worker --prefix` with the same filter. After a crash, add `--previous`.
5. **Stage child output.** Lines of `logger: hwplatform.stage.child` are the child's stdout (engine messages, `ngspice`, `kicad-cli`, router output), one record per line with the run's fields; the last five are also in the activity's `ApplicationError` message (Temporal UI, failure details):
   ```
   ... | jq -r 'select(.run_id=="<run_id>" and .logger=="hwplatform.stage.child") | .msg'
   ```
   The full stage artifacts (`logs` output: `*.log`, the last 200 stdout lines) are in the run's artifacts in the UI or `GET /runs/{id}/artifacts`.
6. **Metrics for context.** `hwp_activities_failed_total{error_class=...}` and `hwp_stage_child_peak_rss_bytes` tell if this is one run or a trend (below).

Tip: `kubectl logs --since=2h | jq -c 'select(.level=="error" or .level=="warning")'` lists everything that went wrong in the last two hours, any component.

## Common failures

| Symptom | What it means | What to do |
|---|---|---|
| `error_class: ToolMissing`, "tools missing on this worker: ..." (not retried) | The worker image lacks a tool the stage declares (`kicad-cli`, `ngspice`, `freerouting`/`krt`). Typical after an image change, or the stage was routed to a queue whose worker is the wrong image. | Check `kubectl exec deploy/hwp-worker -- kicad-cli --version`; fix the image (`deploy/images`), roll the worker. Then start the run again: a failed run is not resumed, the stage cache makes the earlier stages cheap. |
| `error_class: WorkerLost`, "killed (SIGKILL, out of memory?)" | The kernel killed the stage child: the pod hit its memory limit (`worker.resources.limits.memory`, 1.5 GiB in the reduced profile). Retried per stage policy, usually fails again. | Compare `hwp_stage_child_peak_rss_bytes` for that stage with the limit. `kubectl describe pod` shows `OOMKilled` if the worker itself died. Raise the limit or lower the stage's parallelism (`layout` slots); on the shared VPS check no other app took the memory. |
| `error_class: ToolCrashed` | The tool exited non-zero without a result. Retried, then the run fails. | Step 5: the child output holds the tool's message. If it is reproducible, it is a design or engine bug, not infrastructure. |
| `error_class: StoreUnavailable` | S3 (SeaweedFS) or Postgres did not answer the child. Retried with backoff. | `kubectl get pods`; `hwp-seaweedfs-0` and `hwp-postgres-0` logs; a full PVC (`df` in the pod) is the usual cause. The run recovers when the store is back. |
| `error_class: DesignInvalid` (not retried) | `design.yaml` or `requirements.yaml` is wrong; the message names the check. | Fix the design in the UI, upload a new version, run again. |
| `error_class: LlmKeyInvalid` (not retried), design run | The tenant's Claude API key was rejected (revoked, wrong, or the account has no credit). The key itself never appears in logs. | Ask the tenant admin to replace it (Settings, API key; `PUT /api/v1/tenant/llm-keys/anthropic` validates the new key before storing it). `GET /api/v1/tenant/llm-keys/anthropic` shows when it was last validated. |
| `error_class: LlmRateLimited`, or `429` from Anthropic in the agent logs | The tenant's key hit Anthropic's rate limit. Retried with backoff (`Retry-After` honoured). | Wait, or ask the tenant to raise the limit on their Anthropic account. Many of these for one tenant: they are running too many design runs at once; lower `max_concurrent_runs`. |
| `429` on `POST /runs` with `code: quota_exceeded` | The tenant already has `max_concurrent_runs` active runs (default 2). Not an error of the platform. | Wait for a run to finish or cancel one; raise the tenant quota if it is a plan decision (product call). `429` with `Retry-After` on `/auth/*` is the login rate limiter, a different thing. |
| Run stuck in `queued` or `running`, no log lines | No worker polls the queue, or the workflow is waiting. | `kubectl get pods` for `hwp-worker`; `curl` its `/readyz` (503 = Temporal not connected: look for "cannot reach Temporal" warnings); Temporal UI "Pending activities". Metric `hwp_activity_slots_in_use` equal to `hwp_activity_slots_max` for a long time = all slots busy (long layout), not a fault. |
| UI events arrive late or not at all, run page updates only after a refresh | Relay lag: outbox rows are not being published to NATS (SSE falls back to polling the outbox every 5 s, so streams still work, slower). | See "Relay lag" below. |

## Relay lag

Metrics (API port, `/metrics`): `hwp_outbox_unpublished_rows` (and its twin `hwp_relay_backlog`) should sit near 0; `hwp_relay_lag_seconds` is the age of the oldest unpublished row; `hwp_relay_leader` summed over replicas must be 1; `hwp_relay_published_total` must increase while there is traffic.

```
kubectl -n hwflow-platform logs deploy/hwp-api --since=30m | jq -c 'select(.logger=="hwplatform.relay")'
kubectl -n hwflow-platform exec hwp-postgres-0 -- psql -U hwflow -d hwflow -c "select count(*), min(created_at) from outbox where published_at is null"   # as the owner role, RLS applies to others
kubectl -n hwflow-platform exec hwp-nats-0 -- wget -qO- localhost:8222/healthz
```

* `outbox relay error; reconnecting` with `error_class: UndefinedTableError`: the migration has not run yet (right after install); it clears when the `migrate` Job finishes.
* The same with `ConnectionRefusedError` / NATS errors: NATS is down or its PVC is full; the backlog grows and drains by itself when NATS is back (events are in Postgres, nothing is lost).
* `hwp_relay_leader` is 0 everywhere: the relay role cannot log in (`hwflow_relay` password in the Secret differs from the database) or `HWPLATFORM_RELAY_ENABLED=false` on every replica.
* Leader 1, backlog growing, no errors: the leader's connection is stuck. Restart the API pod; the advisory lock is released with the session.

## Metrics

| Where | Port | Path |
|---|---|---|
| API (`hwplatform-api`) | 8080 | `/metrics` (not routed by the Ingress or `nginx-platform.conf`; scrape inside the cluster, or `kubectl port-forward`) |
| Worker | 8081 (`HWPLATFORM_WORKER_HEALTH_PORT`) | `/metrics`, next to `/livez` and `/readyz` |
| Temporal SDK (worker) | 9464, only if `HWPLATFORM_TEMPORAL_METRICS_BIND=0.0.0.0:9464` | `/metrics`, `temporal_*` series (pollers, task latency, slots) |

Series (all `hwp_`, plus the default `process_*` and `python_gc_*`):

* API: `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}` (route is the template; SSE routes report the time to the response head), `sse_connections`.
* Event relay (inside the API, or `hwplatform-relay`): `relay_leader`, `relay_published_total`, `relay_backlog`, `outbox_unpublished_rows`, `relay_lag_seconds`.
* Worker: `activities_started_total`, `activities_completed_total`, `activities_failed_total` (labels `queue`, `activity`, `stage`; failed adds `error_class`), `stage_duration_seconds{queue,stage,outcome}`, `stage_child_peak_rss_bytes{stage}` (the child and the tools it ran), `activity_slots_in_use{queue}`, `activity_slots_max{queue}`.

Quick look without a metrics store: `kubectl -n hwflow-platform exec deploy/hwp-api -- python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8080/metrics').read().decode())" | grep -E '^hwp_(outbox|relay|sse)'`.

## Optional: VictoriaMetrics and Grafana

```
helm upgrade hwp deploy/helm/hwflow-platform -n hwflow-platform -f deploy/helm/hwflow-platform/values-reduced.yaml \
  --reuse-values --set observability.enabled=true            # add --set observability.temporalSdkMetrics=true for the SDK port
kubectl -n hwflow-platform port-forward svc/hwp-grafana 3000:3000      # dashboard "hwflow platform" in the folder "hwflow"
```

VictoriaMetrics (single node, 7 days, 1 GiB PVC) scrapes the API and every worker pod (DNS service discovery of headless Services) every 30 s. Grafana has the data source and the dashboard provisioned from the chart (`dashboards/hwflow-platform.json`), no sign-up, no Ingress. Set the admin password with the optional Secret key `grafana-admin-password`. Measured memory is in `docs/platform-k8s.md`.

## Tracing (off)

`HWPLATFORM_OTEL_ENABLED=true` adds the Temporal SDK's `TracingInterceptor` to the worker and puts `trace_id`/`span_id` in the log lines whenever a span is active. It needs `opentelemetry-sdk` and an exporter configured in the image; no collector is deployed in M1, so leave it off.
