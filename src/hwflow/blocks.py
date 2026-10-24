"""Compatibility alias: this module moved to hwflow.core.blocks (issue #5). Importing it returns the new module itself, so
attribute reads and monkeypatching through the old name act on the real module. Remove after the M1 release, no
later than 2026-12-31 (docs/import-graph.md)."""

import sys

from .core import blocks as _m

from .verify import blocks as _x

# block authoring moved to hwflow.verify.blocks; the old names stay reachable through the alias
for _n in ['extract', 'verify']:
    setattr(_m, _n, getattr(_x, _n))

sys.modules[__name__] = _m
