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
RUN mkdir -p src/hwflow harness/src && touch src/hwflow/__init__.py README.md \
 && uv sync --frozen --no-dev --package hwplatform
RUN useradd -m -u 10001 app
USER app
ENV HWPLATFORM_HOST=0.0.0.0
EXPOSE 8080
CMD ["hwplatform-api"]
