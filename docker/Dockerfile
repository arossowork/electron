# hwflow toolchain + repo in one image: KiCad 10 (kicad-cli and libraries), ngspice, Java + Freerouting,
# KiCadRoutingTools with its prebuilt Rust router, uv, and the Claude Code CLI.
# Build from the repository root:  docker build -f docker/Dockerfile -t hwflow .
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG FREEROUTING_VERSION=2.5.0
ARG KRT_COMMIT=78e3c35330fd38206b7d4773f315ce2bbeea2718
ARG UID=1000

# KiCad 10 from the official PPA. No 3D models (kicad-packages3d is about 6 GB): `pcb render` then draws the bare
# board, and every other stage is unaffected. The symbol and footprint libraries match the kicad-cli version.
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common gpg-agent \
 && add-apt-repository -y ppa:kicad/kicad-10.0-releases \
 && apt-get update && apt-get install -y --no-install-recommends \
      kicad kicad-symbols kicad-footprints \
      ngspice openjdk-21-jre-headless \
      git curl ca-certificates locales tini xvfb xauth libgl1-mesa-dri \
 && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
 && apt-get purge -y software-properties-common && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc/kicad* /usr/share/kicad/demos

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

# The image's own user: Claude Code refuses some modes as root, and files in mounted volumes keep the host owner.
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u ${UID} -s /bin/bash hw \
 && mkdir -p /opt/tools /opt/venv /app && chown -R hw:hw /opt/tools /opt/venv /app
USER hw
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    PATH=/home/hw/.local/bin:/opt/venv/bin:$PATH \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/tools/python \
    KICAD_CLI=/usr/bin/kicad-cli KICAD_SHARE=/usr/share/kicad \
    FREEROUTING=/opt/tools/freerouting.jar JAVA=/usr/bin/java \
    KRT_DIR=/opt/tools/KiCadRoutingTools \
    CLAUDE_CONFIG_DIR=/home/hw/.claude

# Claude Code CLI (native installer, no Node needed). CLAUDE_CONFIG_DIR keeps the login, settings and transcripts
# in one folder, /home/hw/.claude, which compose and k3s mount as a volume.
RUN curl -fsSL https://claude.ai/install.sh | bash && claude --version

RUN curl -fsSL -o /opt/tools/freerouting.jar \
      https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar \
 && git clone -q https://github.com/drandyhaas/KiCadRoutingTools.git $KRT_DIR \
 && git -C $KRT_DIR checkout -q $KRT_COMMIT && rm -rf $KRT_DIR/.git

# Python deps first (cached unless pyproject/uv.lock change), then the router binary built against that venv.
WORKDIR /app
# The workspace member harness/ needs its pyproject.toml here for the lock file to resolve.
COPY --chown=hw:hw pyproject.toml uv.lock .python-version ./
COPY --chown=hw:hw harness/pyproject.toml harness/pyproject.toml
RUN uv sync --frozen --no-install-workspace --group dev \
 && cd $KRT_DIR && /opt/venv/bin/python build_router.py

COPY --chown=hw:hw . .
RUN uv sync --frozen --group dev && hwflow --help >/dev/null

COPY --chown=hw:hw docker/entrypoint.sh /usr/local/bin/entrypoint.sh
EXPOSE 8765
ENTRYPOINT ["tini", "--", "entrypoint.sh"]
CMD ["bash"]
