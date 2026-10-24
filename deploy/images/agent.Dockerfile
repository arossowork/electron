# hwplatform agent worker image (issue #15): `hwplatform-worker --queues hwflow-agent`, the Claude Agent SDK with
# its bundled Claude Code binary (native, no Node), hwharness.prompts and the prompt files (.claude/agents, the
# /hw-design skill, CLAUDE.md, docs, the firmware framework). No KiCad, ngspice, router or engine: the agent's
# lookups and engine commands run on the engine queue (EngineToolWorkflow, ADR 0001 7.6).
#   docker build -f deploy/images/agent.Dockerfile -t hwplatform-agent:dev .
# Runtime contract (deploy/helm, services/platform/README.md "Agent sandbox"): uid 10001, readOnlyRootFilesystem,
# /scratch on an emptyDir, no service-account token, egress only to the Anthropic API and the platform services.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY harness/pyproject.toml harness/pyproject.toml
COPY harness/src harness/src
COPY services/platform/pyproject.toml services/platform/pyproject.toml
COPY services/platform/README.md services/platform/README.md
COPY services/platform/src services/platform/src
# As the API image: only the stdlib-only engine contract modules (hwflow.artifacts, runs, ids), not the engine and
# its numeric dependencies. hwharness.prompts needs only the stdlib and PyYAML.
COPY src/hwflow/artifacts.py src/hwflow/runs.py src/hwflow/ids.py /contracts/hwflow/
RUN touch /contracts/hwflow/__init__.py
RUN uv sync --frozen --package hwplatform --extra agent --no-dev --no-editable \
      --no-install-package hwflow --no-install-package numpy --no-install-package scipy \
      --no-install-package shapely --no-install-package pillow --no-install-package ruamel-yaml \
      --no-install-package ruamel-yaml-clib
# The prompt files the stage prompts are built from (hwharness.prompts, HWFLOW_PROMPTS_DIR) and the read-only
# reference copies a session gets in .hwflow-ref/ (hwplatform.agent.workspace).
COPY .claude/agents /prompts/.claude/agents
COPY .claude/skills/hw-design /prompts/.claude/skills/hw-design
COPY CLAUDE.md /prompts/CLAUDE.md
COPY docs/design-yaml.md docs/hwsw-codesign.md docs/layout-flow.md /prompts/docs/
COPY fw/framework /prompts/fw/framework

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tini ca-certificates \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
 && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin hwp
COPY --from=build /opt/venv /opt/venv
COPY --from=build /contracts /contracts
COPY --from=build /prompts /opt/hwflow/prompts
COPY deploy/images/agent-entrypoint.sh /usr/local/bin/agent-entrypoint.sh
# Everything that writes goes under SCRATCH (an emptyDir; the root fs is read-only). Each session gets its own
# HOME, CLAUDE_CONFIG_DIR and TMPDIR under the worker dir (hwplatform.agent.workspace.Dirs).
ENV SCRATCH=/scratch HOME=/scratch/home TMPDIR=/scratch/tmp \
    HWPLATFORM_WORKER_DIR=/scratch/worker HWPLATFORM_WORKER_QUEUES=hwflow-agent \
    HWFLOW_PROMPTS_DIR=/opt/hwflow/prompts HWPLATFORM_AGENT_PROMPTS_DIR=/opt/hwflow/prompts \
    PYTHONPATH=/contracts PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DISABLE_AUTOUPDATER=1 DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
USER 10001:10001
WORKDIR /scratch
ENTRYPOINT ["tini", "--", "agent-entrypoint.sh"]
CMD ["hwplatform-worker", "--queues", "hwflow-agent"]
