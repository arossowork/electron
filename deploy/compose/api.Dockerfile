# hwplatform API for the dev/compose stack (profile `web`): hwplatform-migrate, then hwplatform-api.
# Only the platform package and its locked dependencies; no KiCad, no engine. Build from the repository root:
#   docker build -f deploy/compose/api.Dockerfile -t hwflow-api .
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app
# The workspace needs the manifests of every member; only hwplatform is installed (--package).
COPY pyproject.toml uv.lock ./
COPY harness/pyproject.toml harness/pyproject.toml
COPY services/platform services/platform
# hwplatform imports only the stdlib-only engine contract modules (hwflow.artifacts, hwflow.runs, hwflow.ids): they
# are copied as a tiny package; the engine and its numeric dependencies are not installed.
COPY src/hwflow/artifacts.py src/hwflow/runs.py src/hwflow/ids.py /contracts/hwflow/
RUN mkdir -p src/hwflow harness/src && touch src/hwflow/__init__.py README.md /contracts/hwflow/__init__.py \
 && uv sync --frozen --no-dev --package hwplatform --no-install-package hwflow --no-install-package numpy --no-install-package scipy --no-install-package shapely --no-install-package pillow --no-install-package ruamel-yaml --no-install-package ruamel-yaml-clib
ENV PYTHONPATH=/contracts
RUN useradd -m -u 10001 app
USER app
ENV HWPLATFORM_HOST=0.0.0.0
EXPOSE 8080
CMD ["hwplatform-api"]
