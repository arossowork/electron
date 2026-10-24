"""Compatibility alias: this module moved to hwflow.layout.board.pcb (issue #5). Importing it returns the new module itself, so
attribute reads and monkeypatching through the old name act on the real module. Remove after the M1 release, no
later than 2026-12-31 (docs/import-graph.md)."""

import sys

from .layout.board import pcb as _m

sys.modules[__name__] = _m
