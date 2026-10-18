#!/usr/bin/env bash
# Installs the EDA toolchain without sudo: ngspice, OpenJDK, KiCad (user Applications), Freerouting.
set -euo pipefail
cd "$(dirname "$0")/.."
brew install ngspice openjdk uv
if [ ! -x ~/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli ] && [ ! -x /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli ]; then
  brew fetch --cask kicad
  DMG=$(brew --cache --cask kicad)
  MNT=$(mktemp -d)
  hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" >/dev/null
  mkdir -p ~/Applications
  ditto "$MNT/KiCad" ~/Applications/KiCad
  hdiutil detach "$MNT" >/dev/null
fi
FR=tools/freerouting-2.5.0.jar
if [ ! -f "$FR" ]; then
  mkdir -p tools
  curl -sSL -o "$FR" https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar
fi
KRT=tools/KiCadRoutingTools
if [ ! -d "$KRT" ]; then
  git clone -q --depth 1 https://github.com/drandyhaas/KiCadRoutingTools.git "$KRT"
fi
uv sync
(cd "$KRT" && ../../.venv/bin/python build_router.py)   # downloads the prebuilt Rust router for this OS
uv run pytest -q
