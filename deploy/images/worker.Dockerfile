# hwplatform worker image (issue #10): the hwplatform-worker entry point, the hwflow engine and the tools the stage
# activities call (ADR 0001 section 4.2): kicad-cli with the symbol and footprint libraries, ngspice, the
# KiCadRoutingTools router, and optionally Java + Freerouting. No Claude CLI, no UI, no KiCad GUI, no 3D models.
#   docker build -f deploy/images/worker.Dockerfile -t hwplatform-worker:dev .
#   docker build -f deploy/images/worker.Dockerfile --build-arg WITH_FREEROUTING=0 -t hwplatform-worker:dev .
# The agent worker (#15) gets its own image later. docker/Dockerfile stays the all-in-one image for local use.
ARG UBUNTU=ubuntu:24.04

# ---------------------------------------------------------------------------------------------- builder
FROM ${UBUNTU} AS build
ARG DEBIAN_FRONTEND=noninteractive
ARG KRT_COMMIT=78e3c35330fd38206b7d4773f315ce2bbeea2718
ARG FREEROUTING_VERSION=2.5.0
ARG WITH_FREEROUTING=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_INSTALL_DIR=/opt/python
WORKDIR /src
# Layer 1: dependencies only (cached until pyproject.toml or uv.lock change). The workspace members need their
# pyproject.toml for the lock file to resolve.
COPY pyproject.toml uv.lock .python-version ./
COPY harness/pyproject.toml harness/pyproject.toml
COPY services/platform/pyproject.toml services/platform/pyproject.toml
COPY services/platform/README.md services/platform/README.md
RUN uv sync --frozen --no-dev --package hwplatform --extra evals --no-install-workspace
# Layer 2: the KRT checkout at the pinned commit and its Rust router built from source against the venv's Python.
# The prebuilt release asset is not used: upstream's latest release moved past the pinned commit (its version no longer
# matches rust_router/Cargo.toml), and a mismatched binary can carry another ABI. The Rust toolchain stays in this stage.
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
RUN mkdir -p /opt/tools && git clone -q https://github.com/drandyhaas/KiCadRoutingTools.git /opt/tools/KiCadRoutingTools \
 && git -C /opt/tools/KiCadRoutingTools checkout -q ${KRT_COMMIT} && rm -rf /opt/tools/KiCadRoutingTools/.git \
 && cd /opt/tools/KiCadRoutingTools && PATH=/root/.cargo/bin:$PATH /opt/venv/bin/python build_router.py --from-source \
 && rm -rf /opt/tools/KiCadRoutingTools/rust_router/target
RUN if [ "$WITH_FREEROUTING" = 1 ]; then curl -fsSL -o /opt/tools/freerouting.jar \
      https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar; fi
# Layer 3: the workspace packages (hwflow, hwharness for the evaluation scorer, hwplatform), installed non-editable so no source tree is needed.
COPY src src
COPY harness/src harness/src
COPY services/platform/src services/platform/src
RUN uv sync --frozen --no-dev --package hwplatform --extra evals --no-editable

# ---------------------------------------------------------------------------------------------- runtime
FROM ${UBUNTU} AS runtime
ARG DEBIAN_FRONTEND=noninteractive
ARG WITH_FREEROUTING=1
ARG WITH_RENDER=0
# WITH_RENDER=1 adds xvfb and Mesa (about 250 MB) so `kicad-cli pcb render` (fab stage, pcb_3d_top.png) has an OpenGL
# context; without it that one step reports FAILED in the stage messages and the stage carries on (export_fab).
# KiCad 10 from the official PPA (matches the all-in-one image). The package pulls the GUI libraries kicad-cli links
# against, but we remove the GUI programs, the demos, templates, help and 3D models in the same layer. One RUN: a
# later rm would not shrink the image. Without WITH_RENDER the Mesa software GL driver (LLVM, 190 MB) is deleted: it is
# only loaded when a program opens an OpenGL context, which kicad-cli does for `pcb render` alone.
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common gpg-agent ca-certificates \
 && add-apt-repository -y ppa:kicad/kicad-10.0-releases \
 && apt-get update && apt-get install -y --no-install-recommends \
      kicad kicad-symbols kicad-footprints ngspice tini locales \
 && if [ "$WITH_RENDER" = 1 ]; then apt-get install -y --no-install-recommends xvfb xauth libgl1-mesa-dri; fi \
 && if [ "$WITH_FREEROUTING" = 1 ]; then apt-get install -y --no-install-recommends openjdk-21-jre-headless; fi \
 && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
 && apt-get purge -y software-properties-common gpg-agent && apt-get autoremove -y \
 && cd /usr/bin && rm -f kicad eeschema pcbnew gerbview pl_editor pcb_calculator bitmap2component \
 && if [ "$WITH_RENDER" != 1 ]; then rm -f /usr/lib/x86_64-linux-gnu/libLLVM.so.* /usr/lib/x86_64-linux-gnu/libgallium-*.so; fi \
 && rm -f /usr/lib/jvm/*/lib/server/classes*.jsa \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man /usr/share/info /usr/share/locale \
      /usr/share/kicad/demos /usr/share/kicad/template /usr/share/kicad/3dmodels /usr/share/kicad/help /usr/share/kicad/internat \
      /var/cache/apt /var/log/*.log \
 && (userdel -r ubuntu 2>/dev/null || true) \
 && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin hwp

COPY --from=build /opt/python /opt/python
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/tools /opt/tools
# Engine data that is not yet part of the hwflow package (blocks/, library/): HWFLOW_ROOT points at it.
COPY blocks /opt/hwflow/blocks
COPY library /opt/hwflow/library

# Everything that writes goes under SCRATCH, which the pod mounts as an emptyDir (readOnlyRootFilesystem: true).
# The entrypoint script creates the directories; SCRATCH-derived paths are overridable with env vars.
ENV SCRATCH=/scratch \
    HOME=/scratch/home \
    XDG_CONFIG_HOME=/scratch/home/.config XDG_CACHE_HOME=/scratch/home/.cache XDG_DATA_HOME=/scratch/home/.local/share \
    KICAD_CONFIG_HOME=/scratch/home/.config/kicad \
    TMPDIR=/scratch/tmp \
    HWFLOW_CAS_DIR=/scratch/cas \
    JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=/scratch/tmp -Duser.home=/scratch/home -XX:MaxRAMPercentage=50" \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    KICAD_CLI=/usr/bin/kicad-cli KICAD_SHARE=/usr/share/kicad JAVA=/usr/bin/java \
    FREEROUTING=/opt/tools/freerouting.jar KRT_DIR=/opt/tools/KiCadRoutingTools \
    HWFLOW_ROOT=/opt/hwflow HWFLOW_TOOLS_DIR=/opt/tools
COPY deploy/images/worker-entrypoint.sh /usr/local/bin/worker-entrypoint.sh
USER 10001:10001
WORKDIR /scratch
# Probes are the pod's job (docs/platform-k8s.md), not a HEALTHCHECK. Override CMD to run another command.
ENTRYPOINT ["tini", "--", "worker-entrypoint.sh"]
CMD ["hwplatform-worker"]
