"""`hwflow block`: the layout block library."""

from __future__ import annotations

from pathlib import Path

from ..core.design import Design
from ..core.libs import Libs


def cmd_block(a) -> int:
    """`hwflow block list|show|check|extract`: the layout block library (src/hwflow/core/blocks.py)."""
    import tempfile

    from ..core import blocks
    from ..verify import blocks as authoring
    if a.what == "match":
        proj = a.project or a.arg
        if not proj:
            print("hwflow block match <project>")
            return 2
        return _match(Design.load(proj), getattr(a, "json", False))
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
        r = authoring.verify(a.arg, w, d)
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
    b = authoring.extract(Path(a.arg), refs, a.name, a.source or "", roles, d, Libs(design=d) if d else Libs())
    f = blocks.write(b, Path(a.out) if a.out else None)
    print(f"wrote {f}: {len(b['roles'])} parts, ports {', '.join(b['ports'])}, {len(b['copper']['tracks'])} tracks, "
          f"{len(b['copper']['vias'])} vias")
    return 0


def _match(d: Design, as_json: bool = False) -> int:
    """`hwflow block match <proj>`: library blocks whose roles, footprints and pin nets fit unbound parts of the design,
    as `blocks:` entries to paste (the block's relative placement and copper become a placement preset)."""
    import json

    from ..core import blocks
    res = blocks.suggest(d)
    matched = {m["block"] for m in res} | {str((e or {}).get("use")) for e in (d.raw.get("blocks") or {}).values()
                                            if isinstance(e, dict)}
    fn = [m for m in blocks.by_function(d) if m["block"] not in matched]
    if as_json:
        print(json.dumps({"schema": "hwflow.block-match/1", "matches": res, "by_function": fn}, separators=(",", ":")))
        return 0
    if fn:  # the parts stage: nothing to match on footprints yet, but the texts name the function or the part
        print(f"{d.name}: {len(fn)} library blocks named by part or function in the project texts (take the block's "
              f"parts and values in the parts stage, or record in notes/parts.md why not):")
        for m in fn:
            print(f"  {m['block']}: {', '.join(m['terms'])} in {', '.join(m['files'][:3])}"
                  f"{' ...' if len(m['files']) > 3 else ''}; {m['description'][:100]}")
    if not res:
        print(f"{d.name}: no library block fits the unbound parts (footprints and pin nets must match a block's roles)")
        return 0
    print(f"{d.name}: {len(res)} library blocks fit unbound parts; add to design.yaml `blocks:` "
          f"(then `hwflow check`):")
    for m in res:
        parts = ", ".join(f"{k}: {v}" for k, v in m["parts"].items())
        print(f"  {m['entry']}: {{use: {m['block']}, parts: {{{parts}}}}}")
        for role, msg in m["value_diffs"].items():
            print(f"    value: {m['parts'][role]} {msg}")
        if m["placed"]:
            print(f"    placement: {', '.join(m['placed'])} have their own `placement:`; remove it (the block places "
                  f"its parts) or give the entry `at: {{x, y, rot}}`")
    return 0
