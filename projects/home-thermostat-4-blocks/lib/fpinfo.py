"""Print bbox, side boxes and pads of the large footprints (floorplan helper)."""
import sys
from pathlib import Path
from hwflow.libs import Libs

proj = Path(__file__).resolve().parent.parent
L = Libs(extra_dirs=[str(proj / "lib/symbols"), str(proj / "lib/footprints")])
for f in sys.argv[1:]:
    d = L.footprint(f)
    print(f, [round(v, 2) for v in d["bbox"]], {k: [round(x, 2) for x in b] for k, b in d.get("sides", {}).items()})
    print("  ", [(q["number"], q["x"], q["y"], q["w"], q["h"], q["kind"]) for q in d["pads"]][:12])
