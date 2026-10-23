#!/usr/bin/env bash
# New worktree for a branch: ../electron-<name> on branch <name>, from <base> (default: the current branch).
# Links tools/ (Freerouting jar, KiCadRoutingTools) to the first worktree that has them and copies projects/
# (untracked designs), so hwflow runs there at once. Prints the path and a free UI port.
#   scripts/new-worktree.sh <name> [base]
set -euo pipefail
name=${1:?usage: scripts/new-worktree.sh <name> [base]}
base=${2:-$(git rev-parse --abbrev-ref HEAD)}
here=$(git rev-parse --show-toplevel)
dest=$(dirname "$here")/electron-$name
git worktree add -b "$name" "$dest" "$base"
tools=""
for w in $(git worktree list --porcelain | sed -n 's/^worktree //p'); do
  if [ -d "$w/tools" ] && [ ! -L "$w/tools" ] && ls "$w/tools"/*.jar >/dev/null 2>&1; then tools="$w/tools"; break; fi
done
[ -n "$tools" ] && [ ! -e "$dest/tools" ] && ln -s "$tools" "$dest/tools"
[ -d "$here/projects" ] && rsync -a --exclude 'build/' "$here/projects/" "$dest/projects/"
port=8765
while ss -ltn 2>/dev/null | grep -q ":$port "; do port=$((port + 1)); done
echo "worktree: $dest (branch $name from $base)"
echo "tools:    ${tools:-not found}"
echo "web UI:   cd $dest && uv run python ui/server.py --port $port"
