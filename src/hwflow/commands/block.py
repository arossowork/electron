"""`hwflow block`: the layout block library."""

from __future__ import annotations

from pathlib import Path

from ..core.design import Design
from ..core.libs import Libs


def cmd_block(a) -> int:
    """`hwflow block list|show|check|extract`: the layout block library (src/hwflow/blocks.py)."""
    import tempfile

    from ..core import blocks
    d = Design.load(a.project) if a.project else None
    if a.what == "list":
        for n in blocks.names(d):
            b = blocks.load(n, d)
            print(f"{n:<28} {str(b.get('description', '')).strip().splitlines()[0][:90]}")
        return 0
    if not a.arg:
        print(f"hwflow block {a.what}: name the {'board' if a.what == 'extract' else 'block'}")
        return 2
    if a.what == "show":
        print(blocks.describe(a.arg, d))
        return 0
    if a.what == "check":
        w = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix=f"block_{a.arg}_"))
        r = blocks.verify(a.arg, w, d)
        for x in r["drc"]:
            if x["sev"] == "error":
                print(f"  {x['type']}: {x['msg']} {x['items']}")
        for n, k in r["split"].items():
            print(f"  net {n} is split in {k} copper islands for post-layout extraction")
        print(f"block {a.arg}: {'OK' if r['ok'] else 'FAIL'}, DRC {r['errors']} errors, {r['warnings']} warnings on the "
              f"block alone; nets joined by its copper: {', '.join(r['joined']) or '-'} ({r['board']})")
        return 0 if r["ok"] else 1
    refs = [r.strip() for r in (a.refs or "").split(",") if r.strip()]
    if not refs or not a.name:
        print("hwflow block extract <board.kicad_pcb> --refs U2,L1 --name <name> --source <text>")
        return 2
    roles = dict(x.split("=", 1) for x in a.roles.split(",")) if a.roles else None
    b = blocks.extract(Path(a.arg), refs, a.name, a.source or "", roles, d, Libs(design=d) if d else Libs())
    f = blocks.write(b, Path(a.out) if a.out else None)
    print(f"wrote {f}: {len(b['roles'])} parts, ports {', '.join(b['ports'])}, {len(b['copper']['tracks'])} tracks, "
          f"{len(b['copper']['vias'])} vias")
    return 0
