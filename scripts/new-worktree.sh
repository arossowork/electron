#!/usr/bin/env bash
# New worktree for a branch: ../electron-<name> on branch <name>, from <base> (default: the current branch).
# Links tools/ (Freerouting jar, KiCadRoutingTools) to the first worktree that has them and copies the designs in
# projects/ that the new branch does not track, so hwflow runs there at once. Prints the path and a free UI port.
#   scripts/new-worktree.sh <name> [base]
set -euo pipefail
name=${1:?usage: scripts/new-worktree.sh <name> [base]}
base=${2:-$(git rev-parse --abbrev-ref HEAD)}
here=$(git rev-parse --show-toplevel)
dest=$(dirname "$here")/electron-$name
git worktree add -b "$name" "$dest" "$base"
tools=""
for w in $(git worktree list --porcelain | sed -n 's/^worktree //p'); do
  if [ -d "$w/tools" ] && [ ! -L "$w/tools" ] && { ls "$w/tools"/*.jar >/dev/null 2>&1 || [ -d "$w/tools/KiCadRoutingTools" ]; }; then tools="$w/tools"; break; fi
done
[ -n "$tools" ] && [ ! -e "$dest/tools" ] && ln -s "$tools" "$dest/tools"
# copy only the designs the new branch does not track, and never overwrite a file there: a copy over tracked files
# leaves local changes and untracked files in the new checkout that block later merges
if [ -d "$here/projects" ]; then
  for p in "$here"/projects/*/; do
    [ -d "$p" ] || continue
    n=$(basename "$p")
    [ -n "$(git -C "$dest" ls-files "projects/$n")" ] && continue
    mkdir -p "$dest/projects/$n"
    rsync -a --ignore-existing --exclude 'build/' "$p" "$dest/projects/$n/"
  done
fi
port=8765
while ss -ltn 2>/dev/null | grep -q ":$port "; do port=$((port + 1)); done
echo "worktree: $dest (branch $name from $base)"
echo "tools:    ${tools:-not found}"
echo "web UI:   cd $dest && uv run python ui/server.py --port $port"
