# Running hwflow in Docker (VPS)

`docker/Dockerfile` builds one x86_64 image with everything `scripts/setup.sh` installs on a Mac: KiCad 10.0.7
(`kicad-cli` with matching symbol and footprint libraries, no 3D models), ngspice 42, OpenJDK 21 with Freerouting
2.5.0, KiCadRoutingTools at a pinned commit with its prebuilt Rust router, uv, the Claude Code CLI, and this
repository with its venv in `/opt/venv`. Every tool path is set by environment variable (`KICAD_CLI`, `KICAD_SHARE`,
`FREEROUTING`, `JAVA`, `KRT_DIR`), so no code change is needed. Commands run under `xvfb-run`, so
`kicad-cli pcb render` works headless.

## Build and check

```
docker build -f docker/Dockerfile -t hwflow .
docker run --rm hwflow uv run pytest -q          # 74 passed, 1 skipped (openEMS) in about 7 min
```

## Log in on the subscription

On any machine with a browser, run `claude setup-token` and put the token in `docker/.env`:

```
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

Or log in once inside the container (`docker compose -f docker/compose.yaml run --rm hw claude`, then `/login`).
The login is saved in the `claude-home` volume. `ANTHROPIC_API_KEY` is always unset.

## Run

```
docker compose -f docker/compose.yaml up -d ui                                  # web UI
ssh -L 8765:127.0.0.1:8765 vps                                                  # then open http://127.0.0.1:8765
docker compose -f docker/compose.yaml run --rm hw scripts/hw-run.sh my-product opus
docker compose -f docker/compose.yaml run --rm hw uv run python evals/harness.py run --models opus --trials 1
```

`projects/` and `evals/runs/` are bind-mounted from the checkout, so results stay on the host. The UI has no login
and starts an agent that can run Bash: publish its port on the host's loopback only and reach it through SSH or a
VPN, never on a public interface.

## Measured cost (examples/sensor_preamp, full build)

| limit | wall time | peak memory (cgroup) |
|---|---|---|
| 2 vCPU, 4 GB | 167 s | 471 MiB |
| 4 vCPU, 8 GB | 162 s | about 450 MiB |

The build is mostly single-threaded (about 1.7 cores on average), so per-core speed matters more than core count.
Each `claude` session adds about 0.3 to 0.5 GB.

## Limits

- x86_64 only: KiCadRoutingTools ships no prebuilt Linux ARM router (an ARM build needs Rust and
  `build_router.py --from-source`).
- No 3D models: the 3D render shows the bare board. Install `kicad-packages3d` (about 6 GB) to get them.
- The image is about 3.8 GB.

## k3s on the VPS

`docker/k8s/` deploys the same image to a single-node k3s (the Hetzner VPS) behind the host nginx and ngrok:

```
ssh root@<vps> kubectl apply -f - < docker/k8s/cluster.yaml   # once: priority classes
docker/k8s/deploy.sh [root@<vps>]                              # build here, import into k3s, apply, nginx route
```

- `hwflow.yaml`: namespace `hwflow`, volumes for projects, eval runs, the Claude login and the cache, and a
  ClusterIP Service. The image is imported into k3s's containerd, never pulled.
- The token: `kubectl -n hwflow create secret generic hwflow-claude --from-literal=token=<claude setup-token>`.
- `nginx-apps.conf` is the VPS's one site: it listens on 127.0.0.1:80 only, ngrok forwards its HTTPS domain to it,
  `/` serves `landing/index.html` (the app selector), and each file in `/etc/nginx/apps.d/` routes a path prefix to
  a Service by its cluster DNS name. A basic-auth login covers every app that does not turn it off.
- `cluster.yaml`: hwflow is `apps-high` with a 1.5 GiB memory request; other apps (singing) are `apps-low` with
  small requests, so the kubelet and the OOM killer pick them before an hwflow run.
