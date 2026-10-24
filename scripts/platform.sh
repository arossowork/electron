#!/usr/bin/env bash
# Reduced platform stack (deploy/compose/platform.yaml): up | down [-v] | status | mem
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dir="$here/deploy/compose"
[ -f "$dir/.env" ] || { echo "no $dir/.env: cp $dir/.env.example $dir/.env and edit it" >&2; exit 1; }
dc() { docker compose -f "$dir/platform.yaml" --env-file "$dir/.env" "$@"; }
cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  up)     dc up -d --wait --remove-orphans "$@" ;;
  down)   dc down --remove-orphans "$@" ;;   # add -v to delete the volumes
  status) dc ps -a ;;
  mem)
    ids=$(dc ps -q)
    # shellcheck disable=SC2086
    docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}' $ids ;;
  *) echo "usage: $0 up|down [-v]|status|mem" >&2; exit 2 ;;
esac
