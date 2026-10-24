#!/bin/bash
# Smoke test of the worker image, run inside it (CI and by hand):
#   docker run --rm --read-only --tmpfs /tmp --tmpfs /scratch:uid=10001,gid=10001,size=1g -v "$PWD/examples:/examples:ro" \
#     -v "$PWD/deploy/images/worker-smoke.sh:/smoke.sh:ro" hwplatform-worker:dev /smoke.sh [sim|layout]
# Runs as the image's non-root user on a read-only root file system; every write goes to /scratch, except ngspice, whose
# tmpfile() (glibc, P_tmpdir) writes to /tmp: mount an emptyDir/tmpfs there too.
set -euo pipefail
until=${1:-sim}
t() { local s=$SECONDS; "$@"; echo "--- $* : $((SECONDS - s)) s"; }
mkdir -p /scratch/work && cp -r /examples/sensor_preamp /scratch/work/ && P=/scratch/work/sensor_preamp  # absolute: the stage children run in other directories
for tool in kicad-cli ngspice; do command -v "$tool" >/dev/null || { echo "missing $tool"; exit 1; }; done
kicad-cli version
test -f "$KRT_DIR/rust_router/grid_router.so" || { echo "KRT router binary missing"; exit 1; }
t hwflow lib show Device:R
t hwflow check $P
t hwflow build $P --until sim
if [ "$until" = layout ]; then t hwflow build $P --until layout; fi
