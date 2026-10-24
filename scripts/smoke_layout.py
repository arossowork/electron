"""Smoke test: place, route and DRC the example design."""
import sys
from collections import Counter
from pathlib import Path
from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow import kicad, pcb, placer
proj = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/sensor_preamp")
d = Design.load(proj); L = Libs()
out = proj / "build"; out.mkdir(exist_ok=True)
name = d.name
parts, cost, viol = placer.place(d, L, seeds=3, iters=20000)
print("place cost", round(cost, 1), viol)
pcb.write_board(out / f"{name}.kicad_pcb", pcb.build_board(d, L, parts))
pcb.export_dsn(d, L, parts, out / f"{name}.dsn")
log = pcb.run_freerouting(out / f"{name}.dsn", out / f"{name}.ses", passes=20)
print([l for l in log.splitlines() if "Auto-routing stage completed" in l or "ERROR" in l][-3:])
tr, vi = pcb.import_ses(out / f"{name}.ses", d)
print(len(tr), "tracks", len(vi), "vias")
pcb.write_board(out / f"{name}.kicad_pcb", pcb.build_board(d, L, parts, tr, vi))
v = kicad.drc(out / f"{name}.kicad_pcb")
print(Counter((x["sev"], x["type"]) for x in v))
for x in v:
    if x["sev"] == "error":
        print(x)
