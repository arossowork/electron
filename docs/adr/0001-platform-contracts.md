# ADR 0001: platform contracts for M1

- Status: accepted for M1 (epic #21)
- Date: 2026-10-24
- Scope: #4, #5, #6, #7, #9, #10, #11, #12, #15, #19 (and the interfaces #16, #17, #18 consume)
- Grounded in: `origin/m1/03-pipeline` (PR #28: `hwflow.pipeline`, `BuildOptions`, `run_build`),
  `origin/m1/02-settings` (PR #24: `hwflow.settings`), `origin/m1/26-auth-hardening` (PRs #25, #27, #29:
  `hwplatform` tenancy, RLS, BYOK) and `deploy/compose/platform.yaml` (PR #23).

This document is the contract the parallel implementation agents follow. Names, signatures, table columns,
queue names and event types below are normative. Where code later has to differ, change this ADR in the same PR.

## 0. Context

Today `run_build` runs `check, sch, sim, layout, post, fab` in the shared `<project>/build/`, skips a stage by an
`inputs_hash` that includes the **whole hwflow source** (editing `fw.py` invalidates everything), and stages read
each other through `build/status.json`, `build/placement.json` and the layout diagnosis history. `layout_auto` runs
its attempts in a fork process pool. `hwplatform` already has `/api/v1`, `Database.tx(tenant_id=)` (SET LOCAL
`app.tenant_id`), `ENABLE` + `FORCE` RLS with policy `tenant_isolation`, role `hwflow_app`,
`tenants.max_concurrent_runs`, `storage.tenant_key()` and `llm_keys.get_tenant_llm_key/mark_invalid/record_usage`.

## 1. Deployables and package boundaries

### 1.1 Packages (uv workspace members)

| Package | Path | Knows about | Never knows about |
|---|---|---|---|
| `hwflow` | `src/hwflow` | designs, tools (kicad-cli, ngspice, KRT, Freerouting, openEMS), local files, `ArtifactStore`/`RunStore` *protocols* and their local implementations | Temporal, NATS, S3, Postgres, HTTP, Claude/LLMs, tenants, inbox/conflicts, eval scores |
| `hwharness` | `harness/` (#4 sets the final path) | Claude Code / Agent SDK invocation, prompt loading from `.claude/`, inbox/conflicts/approval protocol, `score()` | Temporal, NATS, S3, Postgres, HTTP API |
| `hwplatform` | `services/platform` | everything hosted | (it is the top layer) |

`hwplatform` subpackages:

| Subpackage | Content |
|---|---|
| `contracts` | frozen dataclasses for workflow/activity I/O, event types, queue and error names; stdlib only |
| `workflows` | `BuildWorkflow`, `DesignWorkflow`, `EvalWorkflow` (deterministic, queue `hwflow-engine`) |
| `activities` | `stage.py` (engine stages), `records.py` (DB + outbox), `agent.py`, `evals.py` |
| `agent` | Agent SDK session runner, MCP tools, sandbox |
| `events` | envelope, outbox writer, relay, JetStream setup, SSE bridge, consumer helper |
| `storage` | `S3ArtifactStore`, presigning, `tenant_key` (exists) |
| `runs` | `PgRunStore`, `PgStageCache`, repositories for design versions and questions |
| `client` | Temporal client helpers (start/signal/cancel by name), the `hwflow` remote executor plug-in (4.9) |
| `workers` | `hwplatform-worker` entry point |
| `api`, `auth`, `db`, `models`, `tenancy`, `crypto`, `llm_keys` | exist (PRs #25-#29) |

Deployables (M1): `hwplatform-api` (API, relay, SSE), `hwplatform-worker` (all queues), `hwplatform-migrate`
(job), plus the CLI `hwflow` unchanged on developer machines.

### 1.2 Import rules (import-linter contracts)

Engine (`pyproject.toml`, `root_package = "hwflow"`), added to the three existing contracts:

```toml
[[tool.importlinter.contracts]]
name = "hwflow knows no platform, harness, cloud or LLM"
type = "forbidden"
source_modules = ["hwflow"]
forbidden_modules = ["hwharness", "hwplatform", "temporalio", "nats", "boto3", "botocore", "sqlalchemy", "asyncpg",
                     "psycopg", "fastapi", "anthropic", "claude_agent_sdk"]   # hwflow.parts keeps its urllib JLCPCB call

[[tool.importlinter.contracts]]
name = "the build planner is stdlib-only (workflows import it)"
type = "forbidden"
source_modules = ["hwflow.buildplan"]
forbidden_modules = ["hwflow.design", "hwflow.pipeline", "hwflow.settings", "hwflow.libs", "yaml", "pydantic"]
```

`hwflow/__init__.py` stays import-free (it is today), so `import hwflow.buildplan` loads nothing else. Harness
(`root_package = "hwharness"`): forbidden `hwplatform`, `temporalio`, `nats`, `sqlalchemy`, `fastapi`; it uses only
the public `hwflow` API (`pipeline`, `runs`, `artifacts`, `design`). Platform (`root_package = "hwplatform"`):

```toml
[[tool.importlinter.contracts]]
name = "workflows are deterministic: temporalio, hwplatform.contracts and hwflow.buildplan only"
type = "forbidden"
source_modules = ["hwplatform.workflows"]
forbidden_modules = ["hwplatform.activities", "hwplatform.agent", "hwplatform.api", "hwplatform.db", "hwplatform.runs",
                     "hwplatform.storage", "hwplatform.events", "hwplatform.llm_keys", "hwharness", "sqlalchemy",
                     "httpx", "boto3", "nats", "hwflow.pipeline", "hwflow.design", "hwflow.settings",
                     "hwflow.artifacts", "hwflow.runs"]

[[tool.importlinter.contracts]]
name = "the API does not import the engine, the harness or activity code"
type = "forbidden"
source_modules = ["hwplatform.api"]
forbidden_modules = ["hwflow", "hwharness", "hwplatform.activities", "hwplatform.agent", "hwplatform.workflows",
                     "hwplatform.workers"]

[[tool.importlinter.contracts]]
name = "layers"
type = "layers"
layers = ["hwplatform.workers", "hwplatform.api | hwplatform.activities | hwplatform.workflows",
          "hwplatform.agent | hwplatform.client", "hwplatform.runs | hwplatform.events | hwplatform.storage | hwplatform.llm_keys",
          "hwplatform.db | hwplatform.models | hwplatform.tenancy | hwplatform.crypto", "hwplatform.contracts"]
```

The API starts workflows by **name** (`"BuildWorkflow"`) with `hwplatform.contracts` dataclasses and never imports
workflow classes. Rejected: no internal platform contracts, because a workflow importing SQLAlchemy breaks
Temporal's sandbox only at runtime.

## 2. ArtifactStore (#6)

### 2.1 Types (`hwflow/artifacts.py`)

```python
TREE_MEDIA_TYPE = "application/vnd.hwflow.tree+json"

@dataclass(frozen=True)
class ArtifactRef:   # sha256: 64 lowercase hex of the bytes; name: logical, e.g. "board", "fab", "sim.result.json"
    sha256: str; size: int; media_type: str = "application/octet-stream"; name: str = ""

class ArtifactStore(Protocol):
    """Content-addressed bytes. Idempotent puts; the store keeps bytes only, the ref carries the metadata."""
    def put_file(self, path: Path, *, media_type: str = ..., name: str = "") -> ArtifactRef: ...  # streams, hashes while reading
    def put_bytes(self, data: bytes, *, media_type: str = ..., name: str = "") -> ArtifactRef: ...
    def open(self, sha256: str) -> ContextManager[BinaryIO]: ...   # streaming read; raises ArtifactNotFound
    def get_file(self, sha256: str, dest: Path) -> Path: ...        # atomic (tmp + rename), verifies the hash
    def exists(self, sha256: str) -> bool: ...
    def stat(self, sha256: str) -> ArtifactRef | None: ...          # size and media type as first stored

class ArtifactNotFound(KeyError): ...
class StoreUnavailable(OSError): ...   # retryable (network, 5xx); anything else is a bug
def put_tree(store: ArtifactStore, root: Path, *, name: str, include: Callable[[Path], bool] = ...) -> ArtifactRef: ...
def get_tree(store: ArtifactStore, ref: ArtifactRef, dest: Path) -> list[Path]: ...
```

- A **tree** is a JSON artifact `{"schema": "hwflow.tree/1", "files": {"<posix relpath>": {"sha256", "size",
  "mode"}}}` (keys sorted, no timestamps), media type `TREE_MEDIA_TYPE`. Directories (`fab/`, `sim/`, `post/`,
  project file trees) are trees; the tree's own hash identifies the whole directory.
- sha256 everywhere new (the sha1 `_design_hash`/`_inputs_hash` stay for `status.json` display until #7).
- Synchronous API (the engine is synchronous; platform activities calling it run in threads). `put_file` reads
  8 MiB chunks and uses S3 multipart above 64 MiB; `open` streams; nothing loads a large file into memory.

### 2.2 Implementations and key layout

| Implementation | Package | Layout |
|---|---|---|
| `LocalCAS(root)` | `hwflow.artifacts` | `<root>/sha256/<aa>/<sha256>` (aa = first two hex), write tmp + `os.replace`, files read-only (0444). `root` = new setting `paths.cas_dir` (`HWFLOW_CAS_DIR`, default `~/.cache/hwflow/cas`) |
| `S3ArtifactStore(client, bucket, tenant_id)` | `hwplatform.storage` | `s3://<bucket>/tenants/<tenant_id>/artifacts/<sha256>`, key built with `storage.tenant_key(tenant_id, "artifacts", sha)`; `ContentType` set on first put; put = `HeadObject` then upload if absent |

One S3 store instance is bound to one tenant (no per-call tenant argument). Bucket `S3_BUCKET` (compose creates
it). The activity that collected outputs upserts `artifacts(tenant_id, sha256, size, media_type)` (3.4).
**Scope change for #6**: the S3 implementation is in `hwplatform.storage` (the engine must not import boto3); #6's
"build passes against S3" is a platform test running `execute_stage` with `S3ArtifactStore` on compose SeaweedFS.

### 2.3 Stage declaration (`hwflow/pipeline/spec.py`)

```python
@dataclass(frozen=True)
class StageSpec:
    name: str                       # check | sch | sim | layout_attempt | layout_select | post | fab | em
    version: int                    # bump by hand when output semantics change without a code change
    queue: str                      # task queue (4.2), informational for the engine
    design_sections: tuple[str, ...]  # top-level design.yaml keys read; ("*",) = all
    requirements: bool              # electrical content of requirements.yaml (prose stripped, as today)
    project_files: Callable[[Design], list[str]]   # project relpaths read: spice libs, block.yaml, project libs, approval files
    shared_files: Callable[[Design], list[Path]]   # repository data read (library/, blocks/), content-hashed
    upstream: tuple[str, ...]       # stages whose StageResult (result + outputs) is an input
    state_in: tuple[str, ...]       # project state read: "placement" (--reuse), "layout_history", "monte_carlo"
    settings_groups: tuple[str, ...]  # subset of Settings.key_dump(): () or ("layout", "repair", "router")
    params: tuple[str, ...]         # BuildOptions fields that change the result (e.g. "mc")
    code_modules: tuple[str, ...]   # hwflow modules whose source is hashed
    tools: tuple[str, ...]          # "kicad-cli", "ngspice", "krt", "freerouting", "openems"
    outputs: Mapping[str, str]      # output name -> build-relative path or dir (dir = tree)
    run: Callable[[StageContext], dict]   # wraps today's stage_* function
    summarize: Callable[[dict], dict]     # <= 4 KiB of the result, for events and workflow decisions
    state_out: tuple[str, ...] = ()

STAGE_SPECS: dict[str, StageSpec]   # the registry; a test checks it covers STAGES
```

In M1 every stage declares `design_sections=("*",)` and `requirements=True`; a narrower list needs a test that
changes an undeclared section and gets identical outputs (a too-narrow key gives stale results silently).

| Stage | upstream | settings | params | tools | outputs |
|---|---|---|---|---|---|
| check | - | - | - | - | - (result only) |
| sch | check | - | - | kicad-cli | `schematic`=`<name>.kicad_sch`, `project`=`<name>.kicad_pro`, `lib_tables` (tree of `fp-lib-table`, `sym-lib-table`) |
| sim | sch | - | mc | ngspice | `sim` (tree `sim/`) |
| layout_attempt | sch | layout, repair, router | seeds, iters, passes, k, seed0 | krt, freerouting, kicad-cli | `board`, `drc`=`<name>.drc.json`, `placement`, `krt_log`, `metrics` |
| layout_select | all `layout_attempt` | - | - | kicad-cli | as `layout` today: `board`, `drc`, `placement`, `dru`, `project`; `state_out`: placement, layout_history |
| post | layout_select, sim | - | - | ngspice | `post` (tree) |
| fab | layout_select, sch | - | - | kicad-cli | `fab` (tree: gerbers, drill, `bom.csv`, `cpl.csv`, `positions.csv`) |

`code_modules` lists the modules of the stage (e.g. layout: `placer, gplace, routing, regionroute, seedroute,
repair, pcb, kicad, keepout, pinaccess, padiso, corridor, partescape, budgets, diagnose, design, libs, sexpr`).
`tests/test_stage_specs.py` computes each stage function's transitive `hwflow` imports with `grimp` (already an
import-linter dependency) and fails if `code_modules` misses one. After #5 the lists become subpackages
(`hwflow.layout`, `hwflow.core`, ...). `fw.py` is in no list, which closes #6's first done-when.

### 2.4 Cache key

```python
def cache_key(spec: StageSpec, inp: StageInput, *, tool_versions: Mapping[str, str]) -> str:  # 64 hex
    return sha256(canonical_json({
        "schema": "hwflow.stagekey/1", "stage": spec.name, "version": spec.version,
        "code": code_hash(spec.code_modules),            # sha256 over sorted (module, source bytes)
        "design": {k: raw[k] for k in sections},         # parsed YAML, as _design_hash does (comments ignored)
        "requirements": stripped_requirements or None,
        "files": {rel: sha256} for project and shared files,
        "upstream": {stage: {name: ref.sha256 for outputs} | {"result": result_ref.sha256}},
        "state": {name: ref.sha256 or None},
        "settings": {g: snapshot_key_dump[g] for g in spec.settings_groups},
        "params": {p: inp.params[p]},
        "tools": {t: tool_versions[t] for t in spec.tools},  # `kicad-cli version`, `ngspice -v`, KRT git sha, jar sha256
    }).encode()).hexdigest()
```

`canonical_json` = `json.dumps(sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)`. No absolute
path is in a key, so the same input gives the same key on every pod and laptop. The stage cache:

```python
class StageCache(Protocol):   # (stage, cache_key) -> StageResult
    def get(self, stage: str, key: str) -> StageResult | None: ...
    def put(self, stage: str, key: str, result: StageResult) -> None: ...
```

`LocalStageCache` = `<cas_dir>/keys/<stage>/<key>.json`; platform = table `stage_cache` (3.4), per tenant (never
shared across tenants). Only `pass`/`fail`/`blocked` results are cached, never `error` or cancelled ones. A hit
returns the stored result with `cached=True`; outputs are downloaded only when a later stage materializes them.
`rebuild=True` skips `get`, not `put`.

### 2.5 Execution in a scratch dir

```python
@dataclass(frozen=True)
class StageInput:
    run_id: str; stage: str
    design: ArtifactRef                       # design.yaml
    project: ArtifactRef                      # tree of the design version (design.yaml, requirements.yaml, notes/, project libs)
    upstream: Mapping[str, "StageResult"]     # declared upstream stages only
    state: Mapping[str, ArtifactRef | None]; settings: Mapping[str, Any]; params: Mapping[str, Any]

@dataclass(frozen=True)
class StageResult:
    stage: str
    verdict: Literal["pass", "fail", "blocked", "error"]   # ok -> pass, not ok -> fail, layout blocked, raised -> error
    cache_key: str; cached: bool
    result: ArtifactRef                       # the full stage dict (what status.json holds today), JSON
    outputs: Mapping[str, ArtifactRef]; state: Mapping[str, ArtifactRef]
    summary: Mapping[str, Any]                # spec.summarize(result)
    seconds: float; tool_versions: Mapping[str, str]
    error: Mapping[str, Any] | None = None    # {"type", "message", "retryable"} when verdict == "error"

def execute_stage(spec: StageSpec, inp: StageInput, store: ArtifactStore, cache: StageCache,
                  scratch_root: Path, *, tool_cache_dir: Path | None = None,
                  progress: Callable[[str], None] = print) -> StageResult
```

`execute_stage` does, in order:

1. Compute `cache_key`; return the cached result on a hit.
2. Create `<scratch_root>/<run_id>/<stage>-<8 hex>/` and **materialize**: the project tree into `project/`, each
   upstream output at its canonical build path (`build/<name>.kicad_pcb`, `build/sim/`, ...), the state files
   (`build/placement.json`, the diagnosis history), and a **synthesized `build/status.json`** with the upstream
   `result` dicts under their stage keys (`layout_select` under `layout`). Today's stage functions run unchanged:
   `_apply_layout_size`, `_fresh_pre_results` and the Monte Carlo reuse read what they read today.
3. Symlink `build/routecache` and `build/simcache` to `tool_cache_dir` (inline: the project's own dirs; worker: a
   pod-local dir). They are accelerators with their own content keys, never inputs or outputs.
4. Run `spec.run(StageContext(design, libs, workdir=project/build, params, progress))` under
   `hwflow.settings.use(Settings.from_snapshot(...))` (new context variable read by `get_settings()`; the key
   groups come from the run's snapshot, `tools`/`paths` from the local environment).
5. Upload the declared outputs and the result JSON, `cache.put`, delete the scratch dir (on `error` first keep a
   `logs` output: `*.log` and the last 200 stdout lines).

A stage function that raises gives `verdict="error"` (type `StageException`), a normal result the planner treats as
a hard failure; the report is still rendered. #6 adds `tests/test_cold_equals_cached.py`: a cold and a fully cached
build of `examples/sensor_preamp` give the same `StageResult`s except `cached` and `seconds`.

### 2.6 As implemented in #6 (amendments to 2.1-2.5)

- **One `layout` spec until #9.** `STAGE_SPECS` has `check, sch, sim, layout, post, fab` (= `STAGES`); #9 splits
  `layout` into `layout_attempt`/`layout_select`. `layout` declares `layout_select`'s outputs plus `auto` (tree
  `build/auto/`), params `reuse, auto, attempts, layout_search, seeds, iters, passes, retry_layout` (`workers` is passed
  as a hint, not keyed). The layout `blocked` pre-check (diagnosis history) stays in the runner, on the project's own
  `build/`, before the stage is scheduled.
- **Upstream found by the guard.** `layout` upstream is `sch, sim` (the placer reads `sim.derating` for hot parts);
  `post` upstream is `layout, sim`. `sch` upstream stays `check` and `sim` upstream `sch` as above.
- **Tool `kicad-libs`**: KiCad's symbol and footprint libraries (hash of file names and sizes under `paths.kicad_share`),
  declared by `check, sch, sim, layout, post` (they all read KiCad symbols); the table above had them only implicitly
  through `kicad-cli`, and `check`/`sim` had no tool.
- **StageSpec additions**: `unkeyed_state` (state read only on a path whose result is never reused; `layout_history`,
  which feeds the diagnosis of a failed layout) and `cache_verdicts` (`layout`: `("pass",)`, the others
  `pass, fail, blocked`). `project_files` returns relpaths present or not, a folder as `"dir/"`; `shared_files`
  returns probe paths present or not (a new file there shadows a library). Check hooks declare their files with
  `hwflow.pipeline.hooks.register_check_inputs` (hwharness: `build/approved.json`, `conflicts.md`, `inbox/`); declared
  `build/...` files go into the project tree.
- **`cache_key(spec, inp, *, tool_versions, design)`**: `design` is the `Design` loaded from the stage's materialized
  project (the declared project files are hashed there), so `execute_stage` materializes the project tree first and
  computes the key second; on a hit the scratch dir is deleted. `key_doc` adds nothing beyond 2.4.
- **`execute_stage(..., rebuild=False, reuse_verdicts=CACHEABLE, keep_scratch=False)`**: the CLI passes
  `reuse_verdicts=("pass",)` (it always skipped passed stages only). The materialized project folder is named after
  the project (`inp.project.name`, the tree ref's name), so a design without `project:` keeps its file names.
- **Portability fixes the scratch dirs required**: schematic UUIDs are `uuid5(design, index)` (same design, same
  bytes); library tables write project libraries as `${KIPRJMOD}/...` (each `--auto` attempt writes its own); the
  SPICE result cache keys an included file by content, not path; `placement.json` keeps `__board_mm__` (the board size
  of the placement, read by `--reuse` instead of `status.json`); `stage_sim` reuses Monte Carlo by
  `spec.inputs_id` (12 hex of the key without params and state), passed in a context variable.
- **`settings.use()`** also sets a process-wide fallback, so threads a stage starts (SPICE pools) see the snapshot.
- **`LocalCAS`** keeps the first media type in `<sha256>.type` and has `local_path(sha)` (hardlink checkouts). The old
  `_inputs_hash` (sha1 of all hwflow sources) is gone; `status.json` `inputs_hash` is the cache key prefix.
- Project state that is not a file in `build/` (`monte_carlo`) is kept as refs in `build/runs/state.json` (3.3).

## 3. RunStore (#7)

### 3.1 Run model (`hwflow/runs.py`)

```python
RunKind = Literal["build", "design", "eval"]

class RunStatus(StrEnum):
    queued = "queued"; running = "running"; waiting = "waiting"          # waiting = on a person (question, key)
    succeeded = "succeeded"; failed = "failed"; cancelled = "cancelled"  # terminal

TRANSITIONS = {"queued": {"running", "cancelled", "failed"},
               "running": {"waiting", "succeeded", "failed", "cancelled"},
               "waiting": {"running", "cancelled", "failed"}}

class StageStatus(StrEnum):
    pending = "pending"; running = "running"; succeeded = "succeeded"; failed = "failed"
    skipped = "skipped"; cancelled = "cancelled"

@dataclass
class StageRecord:   # key: "check", "layout_attempt.3", "layout_select", "agent.parts"; seq: scheduling order
    key: str; stage: str; seq: int; status: StageStatus; verdict: str | None = None   # StageResult.verdict
    cached: bool = False; cache_key: str | None = None; attempt: int = 1             # Temporal activity attempt
    started_at: datetime | None = None; ended_at: datetime | None = None; seconds: float | None = None
    summary: dict = field(default_factory=dict); result: ArtifactRef | None = None
    outputs: dict[str, ArtifactRef] = field(default_factory=dict)
    error: dict | None = None                                                         # {"type", "message", "retryable"}

@dataclass(frozen=True)
class DesignVersionRef:   # id: UUIDv7 (platform) or content_sha256 (local); content_sha256 = hash of the tree
    id: str; content_sha256: str; design: ArtifactRef; requirements: ArtifactRef | None; project: ArtifactRef

@dataclass
class Run:
    run_id: str                  # UUIDv7 string (hwflow.ids.uuid7(), stdlib implementation)
    kind: RunKind; tenant_id: str | None; project_id: str       # tenant None and project = dir name for local runs
    design_version: DesignVersionRef
    status: RunStatus; verdict: Literal["PASS", "FAIL"] | None  # verdict = REPORT.md verdict, None until rendered
    options: dict; options_hash: str   # BuildOptions (or Design/EvalOptions); 12 hex of result-relevant options + key_dump
    settings: dict; code: dict; tools: dict   # Settings.snapshot(); {"hwflow", "git_sha", "stage_versions"}; tool versions
    stages: list[StageRecord]
    artifacts: dict[str, ArtifactRef]   # run level: "status.json", "REPORT.md", "manifest.json", "score.json"
    created_at: datetime; started_at: datetime | None = None; ended_at: datetime | None = None
    error: dict | None = None; parent_run_id: str | None = None; workflow_id: str | None = None

class RunStore(Protocol):
    def create(self, run: Run) -> Run: ...
    def get(self, run_id: str) -> Run: ...
    def list(self, project_id: str, *, limit: int = 50, before: str | None = None) -> list[Run]: ...
    def transition(self, run_id: str, to: RunStatus, *, error: dict | None = None,
                   verdict: str | None = None) -> Run: ...      # raises InvalidTransition
    def upsert_stage(self, run_id: str, rec: StageRecord) -> None: ...   # idempotent on (run_id, key)
    def set_artifacts(self, run_id: str, artifacts: Mapping[str, ArtifactRef]) -> None: ...
    def append_event(self, run_id: str, type: str, data: dict) -> int: ...  # returns seq
    def project_state(self, project_id: str) -> dict[str, ArtifactRef]: ...
    def set_project_state(self, project_id: str, state: Mapping[str, ArtifactRef], run_id: str) -> None: ...
```

`run.status` is about execution; the design verdict is `run.verdict`. A build that runs to the end with a FAIL
verdict is `succeeded` + `FAIL`; `failed` means the run itself broke (non-retryable error, timeout, retries
exhausted). Rejected: ULID, because Postgres has a native `uuid` type and UUIDv7 sorts by time just the same.

### 3.2 manifest.json (schema `hwflow.run/1`)

The manifest is `Run` serialized with `dataclasses.asdict` plus `"schema": "hwflow.run/1"`: times RFC 3339 UTC,
`ArtifactRef` as `{sha256, size, media_type, name}`, stages in `seq` order. JSON Schema at
`docs/schemas/run-1.json` (#7); a schema change that removes or renames a field bumps it to `hwflow.run/2`.

### 3.3 FileRunStore (CLI and dev)

```
<project>/build/runs/<run_id>/manifest.json     # rewritten atomically (tmp + os.replace) on every change
<project>/build/runs/<run_id>/events.jsonl      # append-only, one envelope (5.1) per line
<project>/build/runs/state.json                 # project state refs {name: ArtifactRef, "run_id": ...}
<project>/build/runs/LATEST                     # run_id of the newest *started* run
```

Writes take the existing `build/.status.lock` flock. Artifacts go to `LocalCAS`. Concurrent runs on one project
each write only their own directory and scratch dirs (`build/runs/<run_id>/work/`), which closes #7's "no longer
overwrite each other".

### 3.4 Postgres tables (migration `0004_runs`, #7)

All tables: `tenant_id uuid NOT NULL REFERENCES tenants ON DELETE CASCADE`, index on `tenant_id`, `ENABLE` +
`FORCE ROW LEVEL SECURITY`, policy `tenant_isolation USING (tenant_id = app_tenant_id()) WITH CHECK (tenant_id =
app_tenant_id())`, added to `TENANT_TABLES`, grants to the app role like `projects`. The worker connects as
`hwflow_app` too and opens every transaction with `Database.tx(tenant_id=<from the activity input>)`.

```sql
CREATE TABLE design_versions (id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE, seq integer NOT NULL,   -- 1, 2, ... per project
  content_sha256 char(64) NOT NULL, design_sha256 char(64) NOT NULL, requirements_sha256 char(64),
  project_tree_sha256 char(64) NOT NULL, source text NOT NULL CHECK (source IN ('upload', 'agent')),
  parent_id uuid REFERENCES design_versions, message text NOT NULL DEFAULT '',
  created_by uuid REFERENCES users ON DELETE SET NULL, created_by_run uuid, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, seq), UNIQUE (project_id, content_sha256));
CREATE TABLE artifacts (tenant_id uuid NOT NULL, sha256 char(64) NOT NULL, size bigint NOT NULL,   -- what exists in S3
  media_type text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id, sha256));
CREATE TYPE run_kind AS ENUM ('build', 'design', 'eval');
CREATE TYPE run_status AS ENUM ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled');
CREATE TABLE runs (id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE, design_version_id uuid NOT NULL REFERENCES design_versions,
  kind run_kind NOT NULL, status run_status NOT NULL DEFAULT 'queued', verdict text CHECK (verdict IN ('PASS', 'FAIL')),
  workflow_id text NOT NULL, options jsonb NOT NULL, options_hash char(12) NOT NULL, idempotency_key text,
  settings jsonb, code jsonb, tools jsonb, error jsonb, artifacts jsonb NOT NULL DEFAULT '{}',
  parent_run_id uuid REFERENCES runs ON DELETE SET NULL, requested_by uuid REFERENCES users ON DELETE SET NULL,
  event_seq integer NOT NULL DEFAULT 0,             -- last event seq (5.3), bumped under the row lock
  created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, ended_at timestamptz,
  UNIQUE (tenant_id, idempotency_key));
CREATE INDEX ix_runs_project ON runs (tenant_id, project_id, id DESC);
CREATE UNIQUE INDEX ux_runs_active_workflow ON runs (workflow_id) WHERE status IN ('queued', 'running', 'waiting');
CREATE INDEX ix_runs_active_tenant ON runs (tenant_id) WHERE status IN ('queued', 'running', 'waiting');
CREATE TABLE run_stages (run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE, tenant_id uuid NOT NULL,
  key text NOT NULL, stage text NOT NULL, seq integer NOT NULL, status text NOT NULL, verdict text,
  cached boolean NOT NULL DEFAULT false, cache_key char(64), attempt integer NOT NULL DEFAULT 1,
  summary jsonb NOT NULL DEFAULT '{}', result_sha256 char(64), outputs jsonb NOT NULL DEFAULT '{}', error jsonb,
  started_at timestamptz, ended_at timestamptz, seconds double precision, PRIMARY KEY (run_id, key));
CREATE TABLE run_artifacts (run_id uuid NOT NULL REFERENCES runs ON DELETE CASCADE, tenant_id uuid NOT NULL,
  name text NOT NULL, stage_key text, sha256 char(64) NOT NULL, media_type text NOT NULL, size bigint NOT NULL,
  PRIMARY KEY (run_id, name));                      -- "layout_select/board", "fab", "REPORT.md": listing, GC roots
CREATE TABLE stage_cache (tenant_id uuid NOT NULL, stage text NOT NULL, cache_key char(64) NOT NULL,
  result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), last_hit_at timestamptz,
  PRIMARY KEY (tenant_id, stage, cache_key));
CREATE TABLE project_state (tenant_id uuid NOT NULL, project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  name text NOT NULL, sha256 char(64) NOT NULL, run_id uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, name));
```

`PgRunStore` (`hwplatform.runs`) implements the `RunStore` method set as `async def` (an `AsyncRunStore` Protocol
in `hwplatform.contracts` mirrors 3.1). `transition` does `UPDATE runs SET status=... WHERE id=... AND status =
ANY(:allowed_from)` and treats 0 rows as `InvalidTransition`. The run's manifest is rendered from the rows and
stored as an artifact at run end (`artifacts.manifest.json`).

### 3.5 status.json and REPORT.md as views

`hwflow.runs.render_status(run, store) -> dict` builds today's `status.json` shape: `settings`; `check`, `sch`, `sim`,
`layout` (from `layout_select`), `post`, `fab` with the stage `result` dict plus `inputs_hash` (cache key prefix),
`design_hash`, `at`; `trace` and `score` as today; `updated`. `REPORT.md` = the existing `write_report(d)` on a dir
holding that status.json. **Projection** `hwflow.runs.checkout(run, project_dir, store)`: after each stage record
and at run end, if the run is `build/runs/LATEST`, write `build/status.json`, `build/REPORT.md` (with history) and
copy the outputs to their canonical paths (hardlinks from the CAS when possible). An older run never overwrites a
newer one. Skills, `ui/server.py` and evals keep reading `build/` unchanged (#7 done-when). The platform stores the
same two files as run artifacts.

### 3.6 As implemented in #7 (amendments to 3.1-3.5)

- **One source of truth for the state machines.** `hwflow.runs` and `hwflow.ids` are stdlib-only (with
  `hwflow.artifacts`; import-linter contract "engine contract modules are stdlib-only"). They define `TRANSITIONS`,
  `TERMINAL`, `allowed_from(to)`, `check_transition`, `StageStatus`, `STAGE_TERMINAL`, `stage_update_applies` (the
  late-replay rule), `STAGE_EVENT`, `RUN_EVENT`, `USER_VISIBLE`, `InvalidTransition`, `RunNotFound`, the dataclasses,
  `RunStore`, `run_to_manifest`/`run_from_manifest`, `rfc3339`, `envelope` and `uuid7`. `hwplatform.contracts`,
  `hwplatform.ids` and `hwplatform.runs` import them instead of keeping copies; for that the workflow contract of 1.2
  drops `hwflow.artifacts` and `hwflow.runs` from its forbidden list (they load nothing but the stdlib).
- **Stage records of a build**: a stage that ran is `succeeded` with its verdict (`pass`, `fail`, `blocked`), one that
  raised is `failed` with verdict `error`; stages after a hard failure are `skipped` (`reason: hard_failure:<stage>`).
  State refs go into `StageRecord.outputs` as `state/<name>`. `execute_stage` adds `design_hash` to the summary
  (status.json's `design_hash`). Scratch dirs: `scratch_root = build/runs`, so `build/runs/<run_id>/<stage>-<hex>/`.
- **`checkout(run, project_dir, store, *, output_paths, report=None, runs=None) -> bool`**: `output_paths[stage][name]`
  (build-relative, from the StageSpecs) and `report` (the pipeline's `write_report`) are passed in, because
  `hwflow.runs` cannot import the pipeline. It merges `render_status` into the existing status.json (the `trace`,
  `score` and command entries such as `optimize`, `fw`, `floorplan`, `em` stay; a cached stage whose entry has the
  same `inputs_hash` keeps it), and returns False without writing when the run is not `LATEST`. A run that is not the
  newest renders its REPORT.md in a temporary copy of its design version (run artifact only).
- **status.json content**: unchanged for skills except the new setting `settings.paths.cas_dir` (2.2); compared with the
  previous engine on `examples/sensor_preamp` for `check`, `build --until sim --rebuild`, `report`, `trace` and
  `build --until layout` (timestamps, timings, paths and hashes ignored).

## 4. Temporal (#9, #15, #19)

### 4.1 One planner, two drivers

`hwflow/buildplan.py` (stdlib only) holds the build orchestration that both `run_build` (inline) and
`BuildWorkflow` drive:

```python
@dataclass(frozen=True)
class RunStage:   # an action: run this stage
    key: str; stage: str; params: Mapping[str, Any]; upstream: tuple[str, ...]
@dataclass(frozen=True)
class Done:
    reason: str   # "complete" | "hard_failure:<stage>" | "blocked"

def plan(options: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]], settings_layout: Mapping[str, Any],
         now: float, started: float) -> list[RunStage] | Done   # results: key -> {"verdict", "summary"}
```

`plan` returns every action runnable now (several `layout_attempt.k` at once) and encodes the stage order, `until`,
the hard-failure stop (`check`, `sch`, `error`, `blocked`, unless `force`) and the `layout_auto` search (attempt 1
alone, cutoff at the first final attempt, hopeless limit `auto_max_errors`, budget `budget_s` against `now -
started`, seeds `seed0 + k*seeds`, then `layout_select`). #9 moves this out of `layout_auto` into `plan` +
`hwflow.pipeline.layout.attempt()` / `select()`; inline keeps a process pool for parallel attempts, the workflow
schedules them as activities. Rejected: orchestration written twice, because the two copies would drift.

### 4.2 Task queues and activities

| Queue | Activities | Tools in the image | M1 slots |
|---|---|---|---|
| `hwflow-engine` | all workflows; `resolve_run`, `stage` for `check`, `render_run`, `create_design_version`, `score_run`; local activities `record_*` | Python only | workflows 4, activities 2 |
| `hwflow-sim` | `stage` for `sim`, `post` | ngspice | 1 |
| `hwflow-layout` | `stage` for `layout_attempt`, `layout_select` | KiCad (kicad-cli), KRT, Java + Freerouting | 1 |
| `hwflow-verify` | `stage` for `sch`, `fab`, `em` | kicad-cli, openEMS | 1 |
| `hwflow-agent` | `agent_stage`, `simulate_answer` | Agent SDK (bundled Claude Code CLI), KiCad libraries (share dir only) | 1 |

One generic stage activity, `run_stage_activity(StageActivityInput) -> StageOut`, is registered on every queue
that runs stages; the workflow picks the queue from `QUEUE_OF[stage]` (`hwplatform.contracts`). It calls
`execute_stage` with `S3ArtifactStore(ctx.tenant_id)` and `PgStageCache(db, ctx.tenant_id)`: in M1 the worker that
runs a stage also checks and writes its cache (the key needs that worker's code hash and tool versions), and every
worker has DB access (one pod). In M2 (#20) tool workers lose DB credentials: the activity returns the key and the
workflow writes the cache through a `record_*` local activity. Rejected for M1: publishing worker fingerprints so
the workflow can look up the cache before scheduling, because with one pod it buys nothing.

### 4.3 Activity I/O (in `hwplatform.contracts`, all frozen dataclasses, default Temporal JSON converter)

```python
@dataclass(frozen=True)
class Ref:            # wire form of ArtifactRef
    sha256: str; size: int; media_type: str; name: str = ""
@dataclass(frozen=True)
class RunCtx:         # in every activity input; the activity opens Database.tx(tenant_id=ctx.tenant_id)
    tenant_id: str; project_id: str; run_id: str; workflow_id: str
@dataclass(frozen=True)
class BuildInput:     # settings_overrides: allowlisted keys only (layout.budget_s, layout.seed0, layout.asic.*)
    ctx: RunCtx; design_version_id: str; design: Ref; project: Ref; options: dict
    settings_overrides: dict = field(default_factory=dict); parent_run_id: str | None = None
@dataclass(frozen=True)
class ResolvedRun:    # resolve_run output
    settings: dict; code: dict; tools: dict; state: dict[str, Ref]

# StageActivityInput / StageOut: the wire forms of StageInput / StageResult (2.5), Ref for ArtifactRef, plus ctx,
# key ("layout_attempt.3") and rebuild: bool on the input.

@dataclass(frozen=True)
class BuildOutput:
    run_id: str; status: str; verdict: str | None; report: Ref | None; status_json: Ref | None
    stages: dict[str, dict]       # key -> {"verdict", "cached", "summary"}
```

Rules: payloads stay under 64 KiB (a `PayloadSizeCodec` raises above 256 KiB; `summary` is capped at 4 KiB); no
secret in any payload (Temporal history is plaintext in Postgres).

### 4.4 Workflows

`BuildWorkflow.run(BuildInput) -> BuildOutput`:
1. `record_run(transition=running)` (local activity: `runs` row + `run.started` outbox row, one transaction).
2. `resolve_run` (engine): settings snapshot (`Settings` from the worker env + allowlisted overrides), code and
   tool versions, project state refs.
3. Loop: `actions = plan(...)`; for each `RunStage`, `record_stage(started)` then `execute_activity(
   "run_stage_activity", ..., task_queue=QUEUE_OF[stage])`; gather with `asyncio.wait(FIRST_COMPLETED)`;
   `record_stage(finished)` per result; when `plan` drops a running attempt (cutoff/budget), cancel its handle.
4. `render_run` (engine): `render_status` + `write_report` -> `status.json`, `REPORT.md`, `manifest.json`,
   `score.json` artifacts; sets `runs.verdict`; `set_project_state`.
5. `record_run(transition=succeeded, verdict=...)`; return `BuildOutput`. An activity that fails for good
   (non-retryable or retries exhausted): `render_run` best effort, then `record_run(failed, error)`.

`DesignWorkflow.run(DesignInput{ctx, design_version_id, brief, answerer: "human" | "simulator", stages:
list[str] = DESIGN_STAGES})`: `DESIGN_STAGES = requirements, partition, parts, circuit, review, layout,
firmware`; each is `agent_stage` on `hwflow-agent` (7). After each stage that changed the workspace,
`create_design_version(source="agent")` (engine) records a new version. Questions: 7.3.

`EvalWorkflow.run(EvalInput{ctx, suite, fixtures: list[str], baseline: Ref | None})`: per fixture (sequential in
M1), a child `DesignWorkflow` with `answerer="simulator"` and `parent_run_id`; then `score_run` (engine) reads the
child run manifests and events (not status.json), compares to the baseline, and stores `eval.json` as a run
artifact (#19).

Workflow IDs (Temporal ID reuse policy `ALLOW_DUPLICATE`, conflict policy `USE_EXISTING`):

- Build: `build-<tenant_id>-<project_id>-<content_sha256[:16]>-<options_hash>` (also when an agent tool starts it,
  with `parent_run_id` set)
- Design: `design-<tenant_id>-<project_id>-<run_id>`; Eval: `eval-<tenant_id>-<run_id>`

Starting the same build twice while one runs returns the running one (the API checks `ux_runs_active_workflow` in
the transaction that inserts the run); after it ended, a new run starts and hits the cache. Search attributes
(Keyword, registered by `hwplatform-migrate`): `HwTenantId`, `HwProjectId`, `HwRunId`, `HwRunKind`.

### 4.5 Errors, retries, timeouts, heartbeats

Error types (`ApplicationError.type`, names in `hwplatform.contracts.errors`):

| Type | Raised when | Retryable |
|---|---|---|
| (none) `StageOut.verdict` pass/fail/blocked/error | the stage ran, or the engine raised (`StageException`, deterministic) | n/a: the planner decides |
| `DesignInvalid` | design.yaml does not parse / unknown section, before any tool | no |
| `ToolMissing` | a tool binary/jar is absent or has the wrong version on the worker | no (alert) |
| `ToolCrashed`, `WorkerLost` | a tool died by a signal / crash code; the stage child was OOM-killed (-9) | yes |
| `StoreUnavailable` | S3, Postgres or NATS unreachable, 5xx | yes |
| `LlmRateLimited` | Anthropic 429/529 | yes, `next_retry_delay` = `retry-after` |
| `LlmKeyInvalid`, `QuestionExpired` | Anthropic 401/403 (7.4); no answer in time (7.3) | no |

| Activity | Queue | start_to_close | heartbeat | Retry (initial, backoff, max interval, attempts) |
|---|---|---|---|---|
| `record_*` (local) | engine | 30 s | - | 1 s, 2, 30 s, 10 |
| `resolve_run`, `render_run`, `create_design_version` | engine | 2 min | - | 2 s, 2, 1 min, 5 |
| stage `check` | engine | 5 min | 30 s | 5 s, 2, 2 min, 3 |
| stage `sch`, `fab` | verify | 10 min | 30 s | 5 s, 2, 2 min, 3 |
| stage `sim`, `post` | sim | 60 min | 60 s | 10 s, 2, 5 min, 3 |
| stage `layout_attempt` | layout | `layout.budget_s` + 10 min | 60 s | 10 s, 2, 5 min, 2 |
| stage `layout_select` | layout | 15 min | 60 s | 10 s, 2, 2 min, 3 |
| stage `em` | verify | 120 min | 60 s | 30 s, 2, 5 min, 2 |
| `agent_stage` | agent | 120 min | 120 s | 30 s, 2, 10 min, 5 |
| `simulate_answer`, `score_run` | agent / engine | 10 min | 60 s | 10 s, 2, 2 min, 3 |

`schedule_to_start` is not set (work waits for a slot); the non-retryable types go in `non_retryable_error_types`
of every policy. In a worker the stage runs in a **child process** (`python -m hwflow.pipeline.exec in.json
out.json`, own process group); the activity thread heartbeats every 15 s with the last progress line. On cancel
(`activity.is_cancelled()`) or heartbeat failure: `SIGTERM` to the group, `SIGKILL` after 10 s, scratch removed,
`CancelledError`. Inline calls `execute_stage` in-process. A pod killed mid-stage: heartbeat timeout (60 s), the
activity is retried on the next poller, finished stages are in history and S3 and do not re-run (#9 done-when).

### 4.6 Cancellation

`POST /runs/{id}/cancel` -> `handle.cancel()`. The workflow catches `CancelledError`, cancels in-flight
activities with `ActivityCancellationType.WAIT_CANCELLATION_COMPLETED` (the child process is dead before the run
is marked), then in a non-cancellable scope (`asyncio.shield` of a local activity) runs `record_run(cancelled)`.
A cancelled `DesignWorkflow` also cancels its linked builds (by workflow ID).

### 4.7 Determinism rules (workflows)

- Only `temporalio`, `hwplatform.contracts`, `hwflow.buildplan` and the stdlib are imported (contract 1.2).
- Time: `workflow.now()` (passed to `plan` as `now`); IDs: from inputs or `workflow.uuid4()`; no `random`, no
  env/settings reads, no I/O, no logging other than `workflow.logger`.
- Iteration order is fixed (lists, sorted dict keys); concurrent results are recorded in completion order but the
  planner's decisions depend only on keys, not order (as `layout_auto` today).
- Every change to workflow logic that alters the command sequence goes behind `workflow.patched("<id>")`; replay
  tests (`tests/workflows/test_replay.py`) run stored histories of a build and a design run in CI.

### 4.8 Concurrency quota

Admission in the API (`POST /runs`): in one transaction, `SELECT ... FROM tenants WHERE id = :t FOR UPDATE`,
count `runs` with `status IN ('queued','running','waiting') AND parent_run_id IS NULL`; if `>=
max_concurrent_runs`, answer 429 `quota_exceeded`; else insert the run (`queued`) and commit, then start the
workflow; on a start failure mark the run `failed`. Linked builds started by a design run do not count (they are
part of that run); they are limited by worker slots. Rejected: queueing over-quota runs in a waiting workflow,
because 429 is enough for M1 and needs no scheduler.

### 4.9 The CLI and `--inline`

`hwflow` cannot import Temporal (1.2), so `hwflow build` runs inline by default (`execute_stage`, `buildplan.plan`,
`LocalCAS`, `FileRunStore`, projection to `build/`); `--inline` is accepted and is the M1 default. `hwflow build
--remote [--detach]` (or `HWFLOW_EXECUTOR=remote`) loads `remote = hwplatform.client:RemoteExecutor` from the
entry-point group `hwflow.executors`: it uploads the project as a design version, starts the run, follows
`/runs/{id}/events` and checks `status.json`, `REPORT.md` and the outputs out into `build/`. `hwflow run wait|status
<run_id>` replace the PID polling in `.claude/agents/hw-layout.md` and `SKILL.md` (#9 done-when). **Scope change
for #9**: "`hwflow build` becomes a client" becomes an opt-in remote executor plug-in; the engine never depends on
the platform.

### 4.10 As implemented in #9 (amendments to 4.1-4.9)

- **Layout stages.** The layout is two stage specs: `layout_attempt` (key `layout_attempt.<k>`, one placement and
  route; params `k, auto, reuse, seeds, iters, passes` are keyed, `deadline` is a hint) and `layout_select` (ranks the
  attempts up to the cutoff, copies the chosen one to the canonical paths, diagnosis, layout history; it reads every
  `layout_attempt.<k>` as a keyed upstream). `plan` also takes option `layout_blocked`: the layout is then not run and
  `layout_select` with param `blocked` stands for it. The driver computes that flag: inline before the first
  attempt, the workflow in `resolve_run` (`ResolvedRun.layout_blocked: StageOut | None`, a field the ADR did not
  have). `post` and `fab` read `layout_select`.
- **Attempts cached on pass only (CLI).** Each attempt is its own cache entry. A rebuild after "attempt 1 failed,
  attempt 2 passed" reruns attempt 1, where the old single `layout` entry was reused whole.
- **Stage children.** The engine child is `python -m hwflow.pipeline.exec in.json out.json`, built on
  `run_child(argv, make_store)`. The platform child is `python -m hwplatform.activities.child` (the same
  `run_child` with the tenant's S3 store and `PgStageCache` behind a synchronous adapter). It checks the stage's
  tools first (`ToolMissing`; `layout_attempt` needs one of KRT and Freerouting). Children kill their process group
  when their parent dies. A child's result is a StageResult JSON; a failed child writes `{"error": {type,
  message}}` only, exit code 3.
- **Extra wire types** in `hwplatform.contracts`: `RecordRun`, `RecordStage`, `RecordedRun` (record_run returns the
  run status after the call, so a run cancelled while queued ends the workflow at once), `RenderInput`,
  `RenderOutput`, `StageOut.attempt`. `ArtifactRef` goes over the wire as is (no separate `Ref`). Policies are
  `contracts.STAGE_POLICY` / `RECORD_POLICY` / `ENGINE_POLICY` with the table's numbers.
- **Platform cache reuse.** The platform reuses every cacheable verdict (pass, fail, blocked). The CLI reuses
  passes only, as before.
- **The manifest** is stored by `record_run` after a terminal transition (from the rows, so it shows the final
  status), not by `render_run`. `render_run` stores `REPORT.md`, `status.json`, `score.json` (the status `score`
  card) and the non-attempt stage outputs as run artifacts `<key>/<name>`, and moves the stages' state to the
  project state.
- **Failure handling.** A stage activity that fails for good fails the run: the run is recorded `failed` with the
  error (type, message, retryable), and the workflow ends with a non-retryable `ApplicationError` of the same type.
  Activities that are still running are cancelled first. A dropped layout attempt (cutoff or budget) is cancelled,
  waited for, and recorded as `skipped` with that reason. A cancelled workflow records its running stages as
  `cancelled`.
- **Worker.** `hwplatform-worker` has `--queues` (or `HWPLATFORM_WORKER_QUEUES`). Its default is every queue but
  `hwflow-agent`, which is polled from #15 on (7.6: from #15 the default is every queue; without the SDK the
  worker drops `hwflow-agent`). It serves `/livez` and `/readyz` on `HWPLATFORM_WORKER_HEALTH_PORT`
  (default 8081). On SIGTERM it stops polling and gives running activities `HWPLATFORM_WORKER_SHUTDOWN_GRACE_S`
  (default 100 s, inside the pod's 120 s) before they are cancelled. Stage scratch and tool caches go under
  `HWPLATFORM_WORKER_DIR`. The sandbox passes through `hwflow.buildplan`, `hwflow.artifacts`, `hwflow.ids`,
  `hwflow.runs` and `hwplatform.contracts`.
- **Import contracts.** `hwflow.buildplan` joins the stdlib-only contract. `hwplatform.activities` and
  `hwplatform.client` may import the engine (`ignore_imports` of the "hwplatform imports only ..." contract). A new
  contract keeps the rest of hwplatform from importing them or the worker. Another new contract, "workflows are
  deterministic", forbids I/O modules in `hwplatform.workflows`.
- **CLI.** `hwflow run wait|status <project> [run_id]` takes the project folder, because local runs live in its
  `build/runs/`. The run id defaults to the newest. Wait exits 0 for succeeded and PASS, 1 for any other end
  (including a build process that died), and 2 when `--timeout` (580 s) passes while the run still runs.
  `hwflow build --detach` starts an inline build in its own session (`build/runs/<run_id>.log`, `.pid`). The
  remote executor needs `HWPLATFORM_API_URL` and `HWPLATFORM_TOKEN`, and keeps the platform project of a folder
  in `build/remote.json`.
- **Not in #9.** There are no `DesignWorkflow` or `EvalWorkflow` skeletons (#15, #19). The replay tests of 4.7
  (`tests/workflows/test_replay.py`) need stored histories and come with the first change that needs
  `workflow.patched`.

## 5. Events (#11)

### 5.1 Envelope (CloudEvents 1.0, structured JSON)

```json
{"specversion": "1.0", "id": "<uuid7>", "type": "stage.completed", "source": "hwplatform/worker",
 "subject": "runs/<run_id>/stages/layout_attempt.2", "time": "2026-10-24T10:00:00.123Z",
 "datacontenttype": "application/json", "dataschema": "hwflow.events/stage.completed/1",
 "tenantid": "<uuid>", "runid": "<uuid7>", "seq": 17, "data": {}}
```

`tenantid`, `runid`, `seq` are CloudEvents extension attributes (lower-case by spec). `seq` is per run, from 1, no
gaps. A breaking change to `data` bumps the version in `dataschema`; additive fields do not.
`hwplatform.events.Event` is the dataclass; `hwflow` writes the same envelope to `events.jsonl` (stdlib only).

### 5.2 Type catalogue (v1)

| Type | data |
|---|---|
| `run.queued`, `run.started` | kind, project_id, design_version_id, options, requested_by / workflow_id, code |
| `run.waiting`, `run.resumed` | reason `question` \| `llm_key_invalid`, question_id? |
| `run.completed`, `run.failed`, `run.cancelled` | status, verdict, report Ref, artifact names / error {type, message} / by |
| `stage.started`, `stage.progress` | key, stage, attempt / key, line <= 300 chars (layout attempt end, agent tool call) |
| `stage.completed`, `stage.failed` | key, stage, verdict, cached, seconds, summary / key, stage, error {type, message, retryable}, attempt |
| `stage.skipped` | key, reason (`until`, `hard_failure:<stage>`, `cutoff`, `budget`) |
| `question.asked` | question_id, stage, questions [{id, text, options?, default?}] |
| `question.answered`, `question.expired` | question_id, answered_by, answers / question_id |
| `artifact.created` | name, ref (user-visible run artifacts only: REPORT.md, board, fab, manifest) |
| `design_version.created` | design_version_id, seq, source, content_sha256 |

### 5.3 Outbox (migration `0005_outbox`, #11)

```sql
CREATE TABLE outbox (
  id bigserial PRIMARY KEY, event_id uuid NOT NULL UNIQUE, tenant_id uuid NOT NULL, run_id uuid,
  seq integer, type text NOT NULL, subject text NOT NULL,      -- NATS subject
  envelope jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz);
CREATE INDEX ix_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
CREATE INDEX ix_outbox_run ON outbox (tenant_id, run_id, seq);
CREATE TABLE consumed_events (consumer text NOT NULL, event_id uuid NOT NULL, tenant_id uuid NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (consumer, event_id));
CREATE FUNCTION outbox_notify() ... PERFORM pg_notify('outbox', '') ...; trigger AFTER INSERT FOR EACH STATEMENT.
```

RLS: `tenant_isolation` for the app role (it inserts and reads its tenant's events). A new role `hwflow_relay`
(LOGIN, no BYPASSRLS, created by `postgres-init.sh`) gets `SELECT, UPDATE (published_at)` on `outbox` and a policy
`relay_all ON outbox FOR ALL TO hwflow_relay USING (true)`, the same pattern as the owner policy of 0002.

**Who emits: the `record_*` local activities** (and the API for `run.queued`, `question.answered`,
`design_version.created`), always in the transaction that changes `runs`/`run_stages`/`questions`:
`events.outbox.emit(session, tenant_id, run_id, type, subject, data)` does `UPDATE runs SET event_seq = event_seq
+ 1 ... RETURNING event_seq` and inserts the row. State change and event commit together or not at all. Tool
activities never emit. Rejected: a separate `emit_event` activity called by the workflow, because it splits the
state write from its event (not transactional) and doubles the activity count; rejected: activities publishing to
NATS directly, because a crash between DB commit and publish loses the event.

### 5.4 Relay and JetStream

- Relay = asyncio task in `hwplatform-api`, leader by `pg_try_advisory_lock(hashtext('hwflow.outbox.relay'))`
  (only one API replica relays). Wakes on `LISTEN outbox` or every 1 s; `SELECT ... WHERE published_at IS NULL
  ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED`; publishes each with header `Nats-Msg-Id: <event_id>` and awaits
  the JetStream ack; sets `published_at`. A crash between ack and update re-publishes; JetStream drops the
  duplicate inside its window (#11 done-when). Published rows are deleted after 30 days by `hwplatform-purge`.
- Stream `HWFLOW_EVENTS`: subjects `hwflow.>`, file storage, retention `limits`, `max_age` 7 d, `max_bytes`
  512 MiB, `discard old`, `duplicate_window` 2 h, 1 replica. Created idempotently by `hwplatform-migrate`.
- Subject: `hwflow.<tenant_id>.<type>`, e.g. `hwflow.6f1c...-...-...-....stage.completed` (UUID with dashes, no
  dots, safe as a token).
- Durable consumers (pull, explicit ack, `ack_wait` 30 s, `max_deliver` 10): `notifier` (`hwflow.*.run.completed`,
  `hwflow.*.run.failed`, `hwflow.*.question.asked`), `eval-scorer` (#19, `hwflow.*.run.completed`). A consumer is
  idempotent with `INSERT INTO consumed_events ... ON CONFLICT DO NOTHING` in the same transaction as its side
  effect; 0 rows inserted = already done, ack.

### 5.5 SSE bridge (#12)

`GET /api/v1/runs/{id}/events` (`text/event-stream`): replays from the outbox (`WHERE tenant_id AND run_id AND seq >
Last-Event-ID ORDER BY seq`) as the source of truth, then subscribes to an ephemeral ordered consumer on
`hwflow.<tenant_id>.>` filtered by `runid`, dropping `seq <= last sent`. SSE `id:` = `seq`, `event:` = type,
`data:` = envelope; keep-alive comment every 15 s; closes after `run.completed|failed|cancelled`.
`GET /api/v1/events` streams all events of the caller's tenant (UI notifications), `id:` = outbox `id`.

## 6. API (#12)

Conventions kept from #25-#29: prefix `/api/v1`, bearer JWT, `TenantContext` re-read per request, 404 for
"absent or another tenant's", pydantic schemas in `api/schemas.py`, errors `{"detail": "..."}`, roles
owner/admin/member. New: cursor pagination `?limit=50&cursor=<id>` on lists (UUIDv7 ids sort by time), and
`Idempotency-Key` header accepted on `POST /runs` (stored on the run; same key returns the same run).

| Method and path | Role | Result |
|---|---|---|
| `POST /projects`, `GET /projects`, `GET/DELETE /projects/{pid}` | exists | |
| `POST /projects/{pid}/design-versions` | member | multipart: `design` (design.yaml), `requirements`?, `bundle`? (tar.gz of the project files, 50 MiB cap, paths validated); 201 `DesignVersionOut`, 200 with the existing one when `content_sha256` matches; YAML parse only (no engine import), `DesignInvalid` comes from the `check` stage |
| `GET /projects/{pid}/design-versions`, `GET /design-versions/{id}`, `GET /design-versions/{id}/files/{path}` | member | list; metadata + file list; 302 to a presigned URL |
| `POST /projects/{pid}/runs` | member | body `{kind: build\|design, design_version_id, options}`; 202 `RunOut`, 200 when an identical build is active, 429 `quota_exceeded`, 422 on options outside the allowlist |
| `GET /projects/{pid}/runs?kind=&status=`, `GET /runs/{id}` | member | list; run + `stages[]` |
| `GET /runs/{id}/manifest`, `GET /runs/{id}/report` | member | `manifest.json` (live while running); `REPORT.md` as `text/markdown` |
| `POST /runs/{id}/cancel` | member | 202; 409 if terminal |
| `GET /runs/{id}/events`, `GET /events` | member | SSE (5.5): one run; the whole tenant |
| `GET /runs/{id}/artifacts` | member | `[{name, stage_key, sha256, size, media_type}]` from `run_artifacts` |
| `GET /runs/{id}/artifacts/{name:path}` | member | 302 to a presigned GET (TTL 300 s, `response-content-disposition`); `fab` (a tree) is served as a zip built once and stored as an artifact `fab.zip` by `render_run` |
| `GET /runs/{id}/questions` | member | open and answered questions |
| `POST /questions/{qid}/answer` | member | body `{answers: {id: value}}`; writes the answer (transaction), then signals `answer_question(QuestionAnswer{question_id, answers, answered_by})`; 409 if not open |
| `POST /evals` | admin | body `{suite, fixtures, baseline_run_id?}`; starts `EvalWorkflow` (run kind `eval`) |
| `PUT/GET/DELETE /tenant/llm-keys/anthropic` | exists (admin) | PUT also signals `retry` to the tenant's runs waiting with `llm_key_invalid` |

Presigned URLs use `HWPLATFORM_S3_PUBLIC_URL` (the browser-reachable SeaweedFS endpoint, via ingress); when it is
unset (dev), the API streams the object itself. The key of every URL comes from `tenant_key()`; the API checks the
`run_artifacts`/`artifacts` row under RLS before signing. Table `questions` (migration `0006_questions`, #12):
`id uuid, tenant_id, run_id, stage, status (open|answered|expired|cancelled), questions jsonb, answers jsonb,
answered_by, asked_at, answered_at`, tenant RLS. OpenAPI is FastAPI's `/openapi.json`, committed as
`services/platform/openapi.json` and diffed in CI.

## 7. Agent (#15)

### 7.1 Where it runs

`agent_stage(AgentStageInput{ctx, stage, workspace: Ref, session: Ref | None, answers: dict | None, brief})
-> AgentStageOutput{outcome: "done" | "needs_answers", workspace: Ref, session: Ref, questions: list[dict],
decisions: list[dict], usage: dict}` on `hwflow-agent`. It materializes the workspace tree (the design version's
project tree plus `notes/`) into `/scratch/agent/<run_id>/<stage>/ws/`, runs one Claude Agent SDK session
(`ClaudeSDKClient`, `cwd=ws`, system prompt = the stage's agent file from `.claude/agents/` and the `hw-design`
skill, loaded with `hwharness.prompts`), and returns the new workspace tree and the session transcript as
artifacts. Rejected: the agent loop inside workflow code, because LLM calls are not deterministic.

### 7.2 Tools (MCP server)

An in-process SDK MCP server `hwflow` (`hwplatform.agent.tools`) replaces the shell:

| Tool | Backed by |
|---|---|
| `lib_sym`, `lib_show`, `lib_fp`, `lib_pads` | `hwflow.libs` in-process (read-only, KiCad share dir in the image) |
| `parts_search` | `hwflow.parts` in-process (JLCPCB search over HTTP) |
| `check`, `build` (`until`, `mc`, `reuse`), `trace`, `optimize` | a linked `BuildWorkflow` (`hwplatform.client` start, ID rule 4.4, `parent_run_id`; `until=check` for check; `options.optimize` for optimize), awaited while the activity heartbeats; returns `REPORT.md`, the trace matrix and the summary |
| `ask_product_person(questions)` | ends the session turn: the activity returns `needs_answers` (7.3) |
| `log_decision(text, source)` | appended to `decisions` (the decision log replaces answers.md) |

Before a build tool call the tool stores the workspace as a design version (`put_tree` + `design_versions` row, the
code of `create_design_version`), so the build runs on exactly what the agent wrote. Linked, not child, workflows:
an activity cannot own children; cancelling the design run cancels them by ID (4.6).

### 7.3 Human in the loop

`DesignWorkflow` on `needs_answers`: `record_question` (local activity: `questions` row + `question.asked`,
`run.waiting`), then `await workflow.wait_condition(lambda: qid in self.answers, timeout=timedelta(days=7))`
on signal `answer_question`; on answer `record_question(answered)` + `run.resumed` and the same `agent_stage` again
with `session` (resume) and `answers`; on timeout `question.expired` and the run fails with
`QuestionExpired`. With `answerer="simulator"` (#19) the workflow calls `simulate_answer` (agent queue, the
evals product-person simulator from `hwharness`) instead of waiting. This replaces `inbox/questions*.json` and
`answers.md` in hosted runs; the local harness keeps them.

### 7.4 BYOK

At activity start: `key = await get_tenant_llm_key(db, keyring, tenant_id)`; the key goes only into the
environment of the SDK's CLI subprocess (`ANTHROPIC_API_KEY`), never into payloads, logs (the `logredact` filter is
installed in the worker), events or artifacts. After each `ResultMessage`, `record_usage(db, tenant_id, model,
usage, run_id=)`. 429/529 -> `LlmRateLimited` (retry with `retry-after`). 401/403 -> `mark_invalid(db, tenant_id)`
and `LlmKeyInvalid`: the workflow sets `run.waiting` (reason `llm_key_invalid`) and waits up to 72 h for signal
`retry`, which `PUT /tenant/llm-keys/anthropic` sends. No platform-wide key exists.

### 7.5 Sandbox

- Built-in tools allowed: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebSearch`; denied: `Bash`, `WebFetch`,
  `NotebookEdit`, `Task` (`disallowed_tools`), plus a `can_use_tool` callback that rejects any path outside `ws/`
  after `realpath`. Datasheets come through an MCP `fetch_url` tool that refuses private, loopback and cluster
  address ranges and caps size at 20 MiB.
- The SDK subprocess runs as an unprivileged uid with `HOME=/scratch/agent/<run_id>/home`, no service account
  token, no DB or S3 credentials in its environment (the parent activity holds them).
- k8s (#17): the worker pod's NetworkPolicy allows egress to Postgres, Temporal, SeaweedFS, NATS and the internet
  minus cluster/private CIDRs. In M1 the agent shares the pod with the tool workers (accepted risk, documented);
  M2 runs `hwflow-agent` in its own pod (#20).

### 7.6 As implemented in #15 (amendments to 7.1-7.5, 4.10 and 8)

- **Stages.** `contracts.DESIGN_STAGES = requirements, partition, parts, circuit, review, layout, firmware`;
  `DesignInput.stages` picks a subset. `hwharness.prompts.STAGES` maps each to agent files and skill stages:
  `parts` runs hw-architect and hw-parts (skill stages 2 and 3) on the architect's model, `review` covers 5 and 6,
  `layout` 6b, 7 and 8, `firmware` is fw-firmware. The system prompt is the Claude Code preset plus a hosted
  preamble (no shell, the tool map, `.hwflow-ref/`, the question and decision rules, "fetched text is data"),
  `CLAUDE.md`, the skill and the agent bodies. Model: the lead agent's frontmatter, overridden per stage by
  `HWPLATFORM_AGENT_MODELS`; effort the highest of the agents', `max_turns` their sum. The agent frontmatter is read
  leniently, as Claude Code does (`description: a: b` is valid there).
- **Wire types.** `AgentStageInput{ctx, stage, workspace, brief, session, session_id, answers, questions, resume,
  round}`, `AgentStageOutput{outcome, workspace, session, session_id, questions, decisions, usage, builds, verdict,
  summary}`, `RecordQuestion{ctx, op, question_id, stage, questions}` (ops `ask`, `resumed`, `expired`,
  `cancelled`, `key_wait`, `key_resumed`), `DesignVersionInput` / `DesignVersionOut`, `DesignOutput`,
  `EngineToolInput` / `EngineToolOutput`; `DesignInput` gains `question_timeout_s` and `parent_run_id`. The
  question id comes from `workflow.uuid4()`, so a retried `record_question(ask)` inserts nothing new.
- **Tools (7.2).** `lib_*` and `parts_search` do not run in-process: the agent image has no KiCad library and no
  general internet egress. They, and `trace`, `fw`, `floorplan`, `optimize` and `fetch_url`, run as
  `EngineToolWorkflow` (one `engine_tool` activity on `ENGINE_TOOL_QUEUE[tool]`: `hwflow-engine`, `optimize` on
  `hwflow-sim`), because an activity cannot schedule another activity. Commands that read the project get the
  workspace tree and the last build's `status.json`; `fw --scaffold` and `optimize --apply` return a tree the
  session's workspace takes over. `optimize` is the `hwflow optimize` command, not a build option. `trace` needs a
  build in the same session. Added tools: `fw`, `floorplan`. There is no `approve` tool. `fetch_url` (7.5) runs on
  the engine queue and saves `datasheets/<name>` plus `<name>.txt` (pdftotext) into the workspace.
- **Linked builds.** `check` / `build` store the workspace as a design version, create the build's run row with
  `parent_run_id` and `workflow_id`, and start `BuildWorkflow` under the 4.4 ID with `ALLOW_DUPLICATE` +
  `USE_EXISTING`: a retried call joins the running build, the same build after it ended is a new run of the same
  ID. The activity cancels the builds it started when it is cancelled; the workflow also cancels the ones it
  knows by ID.
- **Questions (7.3).** No timeout by default (`question_timeout_s=None`, set by `HWPLATFORM_AGENT_QUESTION_TIMEOUT_S`
  at run start): how long a person may take is a product decision, and a waiting workflow costs nothing. The
  workspace is saved as a design version when a question is asked, and the stage resumes the same session
  (`resume`) with the answers. After `ask_product_person` the tool box refuses every tool but `log_decision`, and
  the runner interrupts the turn three assistant messages later.
- **Rate limits and keys (7.4).** `LlmRateLimited` is non-retryable in `AGENT_POLICY` (`AGENT_NON_RETRYABLE`): the
  workflow waits on a timer (the API's reset time, else 60 s doubling to 30 min) and runs the stage again from
  the snapshot in the error details, instead of activity retries. A tenant without a usable key (reason `no_key`)
  waits for `retry` like a rejected one and then starts the stage fresh. Usage comes from
  `ResultMessage.model_usage`, per model. A known reset time is waited for in full (up to 24 h per timer,
  `RATE_LIMIT_RESET_MAX_S`), not capped at 30 min, and a `retry` signal ends any rate-limit wait early.
- **Credentials (amends 7.4: "No platform-wide key exists").** The activity no longer calls
  `get_tenant_llm_key`; it resolves the credential through one port, `hwplatform.credentials.CredentialService`
  (`resolve`, `put`, `mark_invalid`, `record_usage`, `limit_wait`), with two adapters: `api_key` (as 7.4,
  `ANTHROPIC_API_KEY`) and `subscription_token` (a `claude setup-token` OAuth token, `CLAUDE_CODE_OAUTH_TOKEN`,
  never together with an API key; its usage-limit message is parsed by `hwharness.limits`, moved there from
  `hwharness.agent`, into the timer). `tenant_llm_keys.credential_type` (migration 0007) says which; still one
  active credential per tenant and provider. The Agent SDK terms forbid offering claude.ai login or subscription
  limits to third parties without approval, so a subscription credential is for the tenants of
  `HWPLATFORM_SUBSCRIPTION_TENANT_IDS` only (empty by default): PUT gives 403 to any other tenant, and `resolve`
  checks the list again every time. For those tenants only, an operator credential mounted from a Kubernetes
  Secret (`k8s_secret`, a read-only file read at each resolve) comes first (`HWPLATFORM_CREDENTIAL_ORDER`,
  default `operator,db`). Every other tenant still has no platform-wide key. The runner also classifies a
  result whose text is a usage-limit message as `rate_limited`.
- **Resume after a crash.** `agent_stage` snapshots the workspace tree and the transcript
  (`CLAUDE_CONFIG_DIR/projects`, media type `application/x-hwflow-agent-session+tar.gz`, whole JSONL lines) every
  `AGENT_SNAPSHOT_EVERY_S` (300 s) and at the end, and heartbeats their refs (no secret). A retried attempt starts
  from the last heartbeat details (`resume="restart"`).
- **Sandbox (7.5).** As 7.5, plus: the built-in tool set is fixed with `tools=` (not only `disallowed_tools`); a
  PreToolUse hook denies as well as `can_use_tool` (a hook deny is final, `can_use_tool` only sees calls the rules
  leave open); the SDK merges the parent's environment into the CLI's, so `cli_path` is a launcher that applies an
  allowlist before it execs the bundled binary; the worker is non-dumpable; `setting_sources=[]`; `.claude/` and
  `.mcp.json` never reach the workspace; reference docs are read-only copies in `.hwflow-ref/`. The CLI is the
  SDK's bundled native binary, so the image needs no Node.
- **Deployment (7.5, 8, supersedes "M1 shares the pod").** The agent worker is its own image
  (`deploy/images/agent.Dockerfile`, no engine) and its own Deployment in both modes (Helm `agent.*`), with an
  egress policy of its own (Postgres, Temporal, SeaweedFS, DNS, HTTPS to public addresses). Measured: about 112 MiB
  for the idle worker and about 200 MiB for a starting session, so a separate pod costs about 112 MiB in the
  reduced stack. 4.10's worker default changes: every queue, and a process without the SDK drops `hwflow-agent`.
- **Open.** The firmware stage cannot run its host tests (gcc, `make test`) without a shell; it needs an engine
  tool for them. `simulate_answer` is #19.

## 8. Reduced (M1) vs full (M2) stack

| Concern | M1 reduced (2 vCPU, 4 GB) | M2 full (#20) |
|---|---|---|
| Worker | one Deployment, one process, one `Worker` per queue sharing a client, slots per 4.2 | one Deployment and image per queue, KEDA on backlog |
| Image | one (engine, KiCad, ngspice, KRT, Freerouting, Agent SDK; #10 measures it) | engine, sim, layout, verify, agent |
| Cache and records | stage cache in the stage activity (all workers have DB access) | cache writes and `record_*` on `hwflow-engine` only; tool workers get S3 only |
| Temporal / Postgres / auth | single server, namespace `default` / one instance / `LocalAuthBackend` | split services / CloudNativePG / Keycloak |
| Relay, agent | relay in `hwplatform-api`; agent shares the worker pod | relay Deployment if needed; agent pod (optional gVisor) |

Queue names, activity names and payloads are identical in both: the split changes only deployment.

## 9. Implementation order and ownership

| Order | Issue | Owns (from this ADR) |
|---|---|---|
| 1 | #4 | `hwharness` package; moves `approval`, `score`, claude -p runner; `hwharness.prompts`; harness import contract |
| 1 (parallel) | #5 | bounded-context subpackages; `StageSpec.code_modules` switch to subpackages afterwards |
| 2 | #6 | `hwflow.artifacts` (2.1, `LocalCAS`, trees), `hwflow.pipeline.spec` (2.3), `cache_key` (2.4), `StageCache` + `LocalStageCache`, `execute_stage` + scratch materialization (2.5), `settings.use()` + `paths.cas_dir`, `hwflow.pipeline.exec` child entry point, `test_stage_specs`, cold == cached test; `hwplatform.storage.S3ArtifactStore` + its compose test (scope change) |
| 3 | #7 | `hwflow.ids.uuid7`, `hwflow.runs` (3.1, `FileRunStore`, `render_status`, `checkout`), `docs/schemas/run-1.json`, `run_build` writes runs; migration `0004_runs` + `PgRunStore` + `PgStageCache` |
| 4 | #9 | `hwflow.buildplan` + `layout.attempt/select` split (4.1), `hwplatform.contracts`, `workflows.build`, `activities.stage` (child process, heartbeats), `activities.records` (without outbox until #11), `hwplatform-worker`, search attributes, `--remote` executor plug-in and `hwflow run wait/status`, skill/agent doc update, replay tests (scope change 4.9) |
| 4 (parallel) | #10 | worker image and slot sizing from 4.2 |
| 5 | #11 | `hwplatform.events` (envelope, catalogue), migration `0005_outbox`, `hwflow_relay` role, `emit()` inside `record_*`, relay, JetStream setup in migrate, consumer helper; can start against a fake emitter right after #7's migration |
| 6 | #12 | endpoints of 6, `0006_questions`, admission/quota (4.8), presign, SSE bridge (5.5), committed OpenAPI |
| 7 | #15 | `workflows.design`, `activities.agent`, `hwplatform.agent` (MCP tools, sandbox, BYOK use), question flow (7.3) |
| 8 | #19 | `workflows.eval`, `simulate_answer`, `score_run` on manifests/events, baseline per run |
| - | #16, #17, #18 | consume: API + SSE (#16); chart values for slots/NetworkPolicy/`hwflow_relay` secret (#17); log fields `tenant_id, run_id, workflow_id, stage, key` (#18) |

Migration numbers are fixed (0004 runs, 0005 outbox, 0006 questions) so the parallel branches do not collide.

### Scope changes to existing issues

- #6: the S3 store lives in `hwplatform.storage` (the engine has the protocol and `LocalCAS`); stage functions are
  wrapped (scratch dir + synthesized `status.json`), not rewritten.
- #7: also owns the Postgres tables of 3.4 (migration 0004), which #25 had deferred to #12.
- #9: `hwflow build` stays inline by default, remote is an entry-point plug-in; #9 also owns `hwflow.buildplan` and
  the split of `layout_auto` into attempt/select activities.
- #11: the emitter is the `record_*` local activities (no `emit_event` activity); the relay runs in the API process.
- #12: adds `questions` (0006), quota admission and SSE replay from the outbox; run tables come from #7.
