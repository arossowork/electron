#!/bin/sh
# Creates the writable directories under the scratch volume (an emptyDir on a read-only root file system), then execs.
# With the WITH_RENDER build (xvfb installed) the command runs under a virtual display for `kicad-cli pcb render`.
set -eu
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$KICAD_CONFIG_HOME" "$TMPDIR" "$HWFLOW_CAS_DIR"
if command -v xvfb-run >/dev/null 2>&1; then
  exec xvfb-run -a -s "-screen 0 1280x1024x24" "$@"
fi
exec "$@"
