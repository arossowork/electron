"""Compatibility alias: this module moved to hwflow.layout.board.pcb (issue #5). Importing it returns the new module itself, so
attribute reads and monkeypatching through the old name act on the real module. Remove after the M1 release, no
later than 2026-12-31 (docs/import-graph.md)."""

import sys

from .layout.board import pcb as _m

from .layout.route import krt as _x

# the router drivers moved to hwflow.layout.route.krt; the old names stay reachable through the alias
for _n in ['KRT_VARIANTS', '_kicad_cli', '_neck_needed', '_pair_clearance', 'freerouting_jar', 'import_ses', 'krt_dir', 'krt_open_nets', 'krt_route', 'krt_unrouted', 'layer_args', 'route_class', 'router_version', 'run_freerouting', 'run_krt', 'seeded']:
    setattr(_m, _n, getattr(_x, _n))

sys.modules[__name__] = _m
