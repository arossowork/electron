# hwplatform API image (also used for hwplatform-migrate and hwplatform-purge): Python slim, only the hwplatform
# package and its runtime dependencies from the locked workspace.
#   docker build -f deploy/images/api.Dockerfile -t hwplatform-api:dev .
# The worker image (KiCad, ngspice, KRT, Freerouting) builds on docker/Dockerfile (issue #10), not on this one.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY harness/pyproject.toml harness/pyproject.toml
COPY services/platform/pyproject.toml services/platform/pyproject.toml
COPY services/platform/README.md services/platform/README.md
COPY services/platform/src services/platform/src
RUN uv sync --frozen --package hwplatform --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin hwp
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HWPLATFORM_HOST=0.0.0.0
USER 10001:10001
EXPOSE 8080
CMD ["hwplatform-api"]
