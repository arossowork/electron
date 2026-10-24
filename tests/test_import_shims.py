"""The old module paths (`hwflow.pcb`, `hwflow.design`, ...) are sys.modules aliases of the moved modules (issue #5).
They are kept for one release (removal: after the M1 release, no later than 2026-12-31, docs/import-graph.md)."""

import ast
import importlib
from pathlib import Path

import pytest

ALIASES = {
    "design": "core.design", "sexpr": "core.sexpr", "libs": "core.libs", "parts": "core.parts",
    "blocks": "core.blocks", "settings": "core.settings",
    "spice": "sim.spice", "simrobust": "sim.simrobust", "optimize": "sim.optimize", "budgets": "sim.budgets",
    "sch": "schematic.sch",
    "pcb": "layout.board.pcb", "kicad": "layout.board.kicad", "routing": "layout.board.routing",
    "keepout": "layout.board.keepout",
    "seedroute": "layout.route.seedroute", "regionroute": "layout.route.regionroute",
    "repair": "layout.route.repair", "prune": "layout.route.prune",
    "feasibility": "layout.feasibility", "diagnose": "layout.diagnose",
    "placer": "layout.place.placer", "gplace": "layout.place.gplace", "asicflow": "layout.place.asicflow",
    "floorplan": "layout.place.floorplan", "cutcap": "layout.place.cutcap",
    "corridor": "layout.place.corridor", "barrier": "layout.place.barrier", "padiso": "layout.place.padiso",
    "partescape": "layout.place.partescape", "pinaccess": "layout.place.pinaccess",
    "postlayout": "verify.postlayout", "em": "verify.em",
}
SRC = Path(__file__).resolve().parents[1] / "src" / "hwflow"


@pytest.mark.parametrize("old,new", sorted(ALIASES.items()))
def test_old_path_is_the_new_module(old, new):
    assert importlib.import_module(f"hwflow.{old}") is importlib.import_module(f"hwflow.{new}")


def test_monkeypatching_through_the_old_name_reaches_the_real_module(monkeypatch):
    from hwflow import spice
    from hwflow.sim import spice as real
    monkeypatch.setattr(spice, "run_ngspice", "patched")
    assert real.run_ngspice == "patched"


def test_names_that_moved_out_of_a_module_stay_reachable_through_its_alias():
    from hwflow import blocks, design, pcb
    from hwflow.layout.route import krt
    from hwflow.verify import blocks as authoring
    from hwflow.verify import checks
    assert pcb.krt_route is krt.krt_route and pcb.route_class is krt.route_class
    assert design.check is checks.check
    assert blocks.verify is authoring.verify and blocks.extract is authoring.extract


def test_the_engine_does_not_import_through_the_aliases():
    """Aliases are for outside callers; every import inside hwflow uses the new path (the layers contract skips them)."""
    bad = []
    for f in sorted(SRC.rglob("*.py")):
        if f.parent == SRC and f.stem in ALIASES:
            continue
        rel = f.relative_to(SRC).with_suffix("").parts
        pkg = ["hwflow"] + list(rel[:-1])
        for n in ast.walk(ast.parse(f.read_text())):
            if not isinstance(n, ast.ImportFrom):
                continue
            base = (pkg[: len(pkg) - n.level + 1] if n.level else []) + (n.module.split(".") if n.module else [])
            if base[:1] != ["hwflow"]:
                continue
            tail = base[1:]
            names = [tail[0]] if tail else [a.name for a in n.names]
            if any(x in ALIASES for x in names):
                bad.append((f.relative_to(SRC).as_posix(), n.lineno))
    assert not bad, bad
