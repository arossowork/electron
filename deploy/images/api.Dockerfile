# hwplatform API image (also used for hwplatform-migrate and hwplatform-purge): Python slim, only the hwplatform
# package and its runtime dependencies from the locked workspace.
#   docker build -f deploy/images/api.Dockerfile -t hwplatform-api:dev .
# The worker image (KiCad, ngspice, KRT, Freerouting) has its own file, deploy/images/worker.Dockerfile (issue #10).
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY harness/pyproject.toml harness/pyproject.toml
COPY services/platform/pyproject.toml services/platform/pyproject.toml
COPY services/platform/README.md services/platform/README.md
COPY services/platform/src services/platform/src
# hwplatform imports only the stdlib-only engine contract modules (hwflow.artifacts, hwflow.runs, hwflow.ids; the
# import-linter contract in pyproject.toml enforces it). They are copied as a tiny package instead of installing the
# engine and its numeric dependencies (numpy, scipy, shapely, pillow): about 200 MB less in the image.
COPY src/hwflow/artifacts.py src/hwflow/runs.py src/hwflow/ids.py /contracts/hwflow/
RUN touch /contracts/hwflow/__init__.py
RUN uv sync --frozen --package hwplatform --no-dev --no-editable --no-install-package hwflow --no-install-package numpy --no-install-package scipy --no-install-package shapely --no-install-package pillow --no-install-package ruamel-yaml --no-install-package ruamel-yaml-clib

FROM python:3.12-slim
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin hwp
COPY --from=build /opt/venv /opt/venv
COPY --from=build /contracts /contracts
ENV PYTHONPATH=/contracts PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HWPLATFORM_HOST=0.0.0.0
USER 10001:10001
EXPOSE 8080
CMD ["hwplatform-api"]
