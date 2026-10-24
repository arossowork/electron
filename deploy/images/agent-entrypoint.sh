#!/bin/sh
# Creates the writable directories under the scratch volume (an emptyDir on a read-only root file system), then execs.
set -eu
mkdir -p "$HOME" "$TMPDIR" "$HWPLATFORM_WORKER_DIR"
exec "$@"
