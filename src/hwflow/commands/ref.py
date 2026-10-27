"""`hwflow ref search <mpn|function words>`: reference designs around a part (core/refs.py)."""

from __future__ import annotations

import json
from pathlib import Path


def add_arguments(sub) -> None:
    p = sub.add_parser("ref", help="reference designs: `ref search <mpn|function words>` lists designs (examples/, "
                                   "projects/, layout blocks, the external open-hardware index) that use the part, "
                                   "with the sub-circuit around it, the source and the licence")
    p.add_argument("what", choices=["search"])
    p.add_argument("query", nargs="+", help="MPN (or family prefix, e.g. MAX17227) and/or function words (boost, ldo)")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--index", help="external index JSON (default settings.paths.oshw_index)")
    p.add_argument("--project", help="also search this project's own blocks/")
    p.add_argument("--json", action="store_true", help="compact JSON (schema hwflow.ref/1)")


def cmd_ref(a) -> int:
    from ..core import refs
    proj = Path(a.project) if a.project else None
    if proj is not None and proj.name == "design.yaml":
        proj = proj.parent
    res = refs.search(a.query, limit=a.limit, oshw_path=Path(a.index) if a.index else None, design_dir=proj)
    print(json.dumps(res, separators=(",", ":")) if a.json else refs.text(res))
    return 0 if res["hits"] else 1
