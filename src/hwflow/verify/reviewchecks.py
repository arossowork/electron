"""Checks for what design reviews kept finding late (docs/review-early.md): cheap, deterministic, from design.yaml,
requirements.yaml and the project files. They run in `hwflow check`.

Codes:
- `footprint-link` (error): a footprint `lib:name` that hwflow finds, but not in the `lib.pretty` folder the KiCad
  library table points `lib` to (two folders with one nickname, e.g. a project library and a block's footprints).
  KiCad would place no footprint for that part (ERC `footprint_link_issues`).
- `mains-fuse` (error): mains nets with no overcurrent device (fuse, PTC, breaker) on them, or no mains input
  conductor that reaches the rest of the circuit only through one. Mains nets: the `nets` of a `kind: mains` region
  and the nets of a `board.isolation` group with `role: mains` (or "mains" in its name or class). Input conductors:
  `nets.<n>.role: mains_input` or the group's `inputs`, else the mains nets on a connector pin.
- `mechanical` (warn): a design with mains nets and no fixing, no cover in the floorplan (`mechanical:` regions), or a
  region named like a fixing or cover that does not say whether it is touchable (`accessible`).
- `model-mpn`: `spice.mpn` (the part the SPICE model or fit comes from) differs from the BOM `mpn` (error); without
  `spice.mpn`, the model's comment block names a near-identical part number that is not the BOM one (warn).
- `cost-limit`: the cost recomputed from design.yaml (BOM prices + `cost.extra`) above a cost requirement's limit
  (error on a must, warn on a should); `cost-doc`: cost.md states a total that design.yaml does not reproduce (error
  when it claims a must limit met, warn otherwise), or prices a part differently from design.yaml (warn).
- `req-file`: a file named in a requirement's text, acceptance or verification does not exist in the project
  (error on a must, warn otherwise).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from ..core.pinchecks import _kind

# --------------------------------------------------------------------------------------------------- requirements


def requirements(d) -> list[dict]:
    import yaml
    p = d.dir / "requirements.yaml"
    if not p.exists():
        return []
    try:
        return [r for r in (yaml.safe_load(p.read_text()) or {}).get("requirements") or [] if isinstance(r, dict)]
    except yaml.YAMLError:
        return []


FILE_RE = re.compile(r"(?<![\w/.\-])((?:[\w.\-]+/)*[\w\-][\w.\-]*\.(?:md|yaml|yml|json|csv|txt|pdf|png|svg|cir|lib|sp|"
                     r"kicad_pcb|kicad_sch|c|h|py|ipynb))(?![\w/])")
URL_RE = re.compile(r"\b\w+://\S+")


def named_files(r: dict) -> list[str]:
    """Project-relative files a requirement names in its text, acceptance or verification (URLs and build/ outputs
    left out: hwflow writes those)."""
    out = []
    for k in ("text", "acceptance", "verification"):
        s = URL_RE.sub(" ", str(r.get(k) or ""))
        for m in FILE_RE.finditer(s):
            f = m.group(1).rstrip(".")
            if f.startswith(("build/", "./build/")) or f in out:
                continue
            out.append(f)
    return out


def req_file_findings(d) -> list[dict]:
    f = []
    for r in requirements(d):
        miss = [x for x in named_files(r) if not (d.dir / x).exists()]
        if miss:
            sev = "error" if str(r.get("priority", "")).lower() == "must" else "warn"
            f.append({"sev": sev, "code": "req-file",
                      "msg": f"{r.get('id')}: names {', '.join(miss)}, which does not exist in the project: write it "
                             f"(the acceptance depends on it) or change the requirement with the product person"})
    return f


# --------------------------------------------------------------------------------------------------------- cost

CUR = r"(?:USD|US\$|\$|EUR|€)"
NUM = r"(\d+(?:[.,]\d+)?)"
LIMIT_RES = [
    re.compile(rf"(<=|≤|=<|<|less than|under|below|at most|no more than|not more than|max(?:imum)?\.?)\s*(?:of\s*)?"
               rf"({CUR})?\s*{NUM}\s*({CUR})?", re.I),
    re.compile(rf"({CUR})?\s*{NUM}\s*({CUR})?\s*(or less|max(?:imum)?|or under)\b", re.I),
]
COST_WORD = re.compile(r"\b(cost|price|BOM)\b", re.I)


def cost_limits(reqs: list[dict]) -> list[dict]:
    """Cost requirements and their limit: `cost_max_usd` on the requirement, else the first currency limit
    ("< 25.00 USD", "<= $12", "25 USD or less") in its acceptance, then its text."""
    out = []
    for r in reqs:
        if r.get("cost_max_usd") is not None:
            out.append({"id": r.get("id"), "priority": str(r.get("priority", "")).lower(),
                        "limit": float(r["cost_max_usd"]), "strict": False, "cur": "USD"})
            continue
        if not COST_WORD.search(f"{r.get('text') or ''} {r.get('acceptance') or ''}"):
            continue
        for k in ("acceptance", "text"):
            s = str(r.get(k) or "")
            hit = None
            for m in LIMIT_RES[0].finditer(s):
                if m.group(2) or m.group(4):
                    hit = (m.group(3), m.group(2) or m.group(4), m.group(1).strip() in ("<", "less than", "under", "below"))
                    break
            if hit is None:
                for m in LIMIT_RES[1].finditer(s):
                    if m.group(1) or m.group(3):
                        hit = (m.group(2), m.group(1) or m.group(3), False)
                        break
            if hit:
                cur = "EUR" if hit[1] in ("EUR", "€") else "USD"
                out.append({"id": r.get("id"), "priority": str(r.get("priority", "")).lower(),
                            "limit": float(hit[0].replace(",", ".")), "strict": hit[2], "cur": cur})
                break
    return out


def cost_doc(d) -> Path | None:
    for rel in ("notes/cost.md", "cost.md"):
        if (d.dir / rel).is_file():
            return d.dir / rel
    return None


ANY_NUM = re.compile(r"(?<![\w.])(\d+\.\d+|\d+)(?![\w.])")


EQ = re.compile(r"(?<![<>!=])=(?!=)")


def doc_total(text: str) -> float | None:
    """The last total a cost document states: a table row whose first cell says "total" (the last number of the row),
    or a sentence "... total [is|stays|=|:] <n>" (with a sum, the number after its last "=")."""
    val = None
    for line in text.splitlines():
        st = line.strip()
        if st.startswith("|"):
            cells = [c.replace("*", "") for c in st.strip("|").split("|")]
            if cells and re.search(r"\btotal\b", cells[0], re.I):
                nums = [n for c in cells[1:] for n in ANY_NUM.findall(c)]
                if nums:
                    val = float(nums[-1])
            continue
        for m in re.finditer(r"\btotal\b", st, re.I):
            seg = re.split(r"(?<=\S)[.;](?=\s|$)", st[m.end():], maxsplit=1)[0].replace("*", "")
            eqs = list(EQ.finditer(seg))
            nums = ANY_NUM.findall(seg[eqs[-1].end():] if eqs else seg)
            if nums and (eqs or re.match(r"\s*(?:is|stays|of|now|:|=|was)?\s*(?:about\s*)?(?:USD|\$)?\s*\d", seg)):
                val = float(nums[0])
    return val


def doc_prices(text: str, d) -> list[str]:
    """Rows of the cost document's tables that price a design part differently from design.yaml: a row that names
    the part (its ref or its MPN) and has a unit-price column."""
    rows = [r for r in d.bom() if r.get("unit_price_usd") is not None]
    out, unit_col = [], None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            unit_col = None if not line.strip() else unit_col
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        heads = [i for i, c in enumerate(cells) if re.search(r"\bunit\b", c, re.I) and not ANY_NUM.search(c)]
        if heads:
            unit_col = heads[0]
            continue
        if unit_col is None or unit_col >= len(cells):
            continue
        nums = ANY_NUM.findall(cells[unit_col].replace("*", ""))
        if not nums:
            continue
        price = float(nums[0])
        head = " ".join(cells[:unit_col])
        for r in rows:
            refs = str(r["refs"]).split(",")
            mpn = str(r.get("mpn") or "")
            named = any(re.search(rf"(?<![\w-]){re.escape(x)}(?![\w-])", head) for x in refs) or \
                (len(mpn) >= 5 and re.search(rf"(?<![\w-]){re.escape(mpn)}(?![\w-])", head, re.I))
            if not named:
                continue
            want = float(r["unit_price_usd"])
            if abs(price - want) > max(0.005, 0.05 * want):
                out.append(f"{r['refs']} at {price:g} (design.yaml {want:g})")
            break
    return out


def cost_findings(d) -> list[dict]:
    lims = cost_limits(requirements(d))
    doc = cost_doc(d)
    if not lims and doc is None:
        return []
    bom, missing = d.cost()
    extra = [x for x in ((d.raw.get("cost") or {}).get("extra") or []) if isinstance(x, dict)]
    ext = sum(float(x.get("usd") or 0) for x in extra)
    total = bom + ext
    how = f"BOM ${bom:.2f}" + (f" + cost.extra ${ext:.2f} ({len(extra)} lines)" if extra else "") + \
          (f"; {len(missing)} BOM lines have no price_usd" if missing else "")
    f = []
    for lim in lims:
        if lim["cur"] != "USD":
            f.append({"sev": "info", "code": "cost-limit", "msg": f"{lim['id']}: limit {lim['limit']:g} {lim['cur']} "
                      f"not compared (design prices are USD; give cost_max_usd on the requirement)"})
            continue
        over = total >= lim["limit"] if lim["strict"] else total > lim["limit"] + 1e-9
        if over:
            f.append({"sev": "error" if lim["priority"] == "must" else "warn", "code": "cost-limit", "decision": True,
                      "msg": f"{lim['id']}: unit cost from design.yaml is ${total:.2f} ({how}), limit "
                             f"{'<' if lim['strict'] else '<='} ${lim['limit']:g}. A cost lever or the limit is a "
                             f"product decision: ask the product person with the numbers"})
    if doc is not None:
        text = doc.read_text(errors="replace")
        rel = doc.relative_to(d.dir).as_posix()
        stated = doc_total(text)
        usd = [x for x in lims if x["cur"] == "USD"]
        for x in usd:  # the document itself says the limit is missed
            if stated is not None and (stated >= x["limit"] if x["strict"] else stated > x["limit"] + 1e-9) \
                    and stated > total + 1e-9:
                f.append({"sev": "error" if x["priority"] == "must" else "warn", "code": "cost-limit", "decision": True,
                          "msg": f"{x['id']}: {rel} states a unit cost of ${stated:.2f}, limit "
                                 f"{'<' if x['strict'] else '<='} ${x['limit']:g}. A cost lever or the limit is a "
                                 f"product decision: ask the product person with the numbers"})
        if stated is not None and abs(stated - total) > max(0.05, 0.02 * total):
            met = [str(x["id"]) for x in usd if x["priority"] == "must" and stated <= x["limit"]]
            f.append({"sev": "error" if met else "warn", "code": "cost-doc",
                      "msg": f"{rel} states a total of ${stated:.2f}, design.yaml gives ${total:.2f} ({how}): "
                             f"put the non-BOM lines (PCB, assembly fees, cables, mechanics) in design.yaml "
                             f"`cost.extra` and the part prices in `price_usd`, so the total is recomputed"
                             + (f"; the document claims {', '.join(met)} met" if met else "")})
        bad = doc_prices(text, d)
        if bad:
            f.append({"sev": "warn", "code": "cost-doc",
                      "msg": f"{rel} prices {len(bad)} parts differently from design.yaml: {'; '.join(bad[:6])}"
                             f"{' ...' if len(bad) > 6 else ''}"})
    return f


# --------------------------------------------------------------------------------------------- footprint links


def footprint_link_findings(d, libs) -> list[dict]:
    """A nickname whose first `.pretty` folder (the one the KiCad library table gets, kicad.write_lib_tables) lacks a
    footprint that hwflow finds in another folder with the same nickname."""
    if libs is None:
        return []
    by_lib: dict[str, list[tuple[str, str]]] = {}
    for ref, c in d.components.items():
        fp = c.get("footprint")
        if fp and ":" in str(fp) and not c.get("virtual"):
            lib, name = str(fp).split(":", 1)
            by_lib.setdefault(lib, []).append((ref, name))
    f = []
    for lib, uses in sorted(by_lib.items()):
        dirs = [b / f"{lib}.pretty" for b in list(libs.extra) + [libs.root / "footprints"] if (b / f"{lib}.pretty").exists()]
        if len(dirs) < 2:
            continue
        table = dirs[0]
        for name in sorted({n for _r, n in uses}):
            if (table / f"{name}.kicad_mod").exists():
                continue
            other = next((x for x in dirs[1:] if (x / f"{name}.kicad_mod").exists()), None)
            if other is None:
                continue
            refs = ", ".join(r for r, n in uses if n == name)
            f.append({"sev": "error", "code": "footprint-link",
                      "msg": f"{refs}: footprint {lib}:{name} is in {other}, but the library table maps {lib} to "
                             f"{table}, which has no {name}: KiCad places no footprint (ERC footprint_link_issues). "
                             f"Copy the footprint into {table.name}, or use the library it is in"})
    return f


# ------------------------------------------------------------------------------------------------- model source

MPN_TOKEN = re.compile(r"(?<![\w-])([A-Z0-9][A-Z0-9\-]{3,}[A-Z0-9])(?![\w-])")


def _norm(s: str) -> str:
    return re.sub(r"[\s_]", "", str(s)).upper()


def _model_block(text: str, name: str) -> str:
    """The comment lines right above `.subckt <name>` / `.model <name>` and the comments inside the subckt."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*\.(subckt|model)\s+(\S+)", line, re.I)
        if not m or m.group(2).upper() != name.upper():
            continue
        j, out = i - 1, []
        while j >= 0 and (lines[j].lstrip().startswith("*") or not lines[j].strip()):
            if lines[j].strip():
                out.insert(0, lines[j])
            j -= 1
        if m.group(1).lower() == "subckt":
            for k in range(i + 1, len(lines)):
                if re.match(r"\s*\.ends", lines[k], re.I):
                    break
                if lines[k].lstrip().startswith("*") or ";" in lines[k]:
                    out.append(lines[k])
        return "\n".join(out)
    return ""


def _near(a: str, b: str) -> bool:
    """Two different part numbers of one family (common prefix of at least 4 characters and 60 % of the shorter) where
    neither is the other's family prefix (TPS61299 / TPS61299DRLR is the same part)."""
    if a == b or a.startswith(b) or b.startswith(a):
        return False
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    rest = min(len(a[n:].strip("-")), len(b[n:].strip("-")))  # one letter: plating, packing or a document suffix
    return n >= max(4, 0.6 * min(len(a), len(b))) and rest >= 2


def model_mpn_findings(d) -> list[dict]:
    f = []
    texts: dict[str, str] = {}
    for ref, c in d.components.items():
        sp, mpn = c.get("spice"), c.get("mpn")
        if not isinstance(sp, dict) or not mpn:
            continue
        bom = _norm(mpn)
        if sp.get("mpn"):
            src = _norm(sp["mpn"])
            if not (src == bom or bom.startswith(src) or src.startswith(bom)):
                f.append({"sev": "error", "code": "model-mpn",
                          "msg": f"{ref}: the SPICE model comes from {sp['mpn']} (spice.mpn), the BOM part is {mpn}: "
                                 f"take the limits from the BOM part's datasheet, or buy the modelled part"})
            continue
        lib, name = sp.get("lib"), sp.get("subckt") or sp.get("model")
        if not lib or not name:
            continue
        p = d.dir / str(lib)
        if str(lib) not in texts:
            texts[str(lib)] = p.read_text(errors="replace") if p.is_file() else ""
        blk = _model_block(texts[str(lib)], str(name))
        defined = {x.upper().split("_")[0] for x in re.findall(r"^\s*\.(?:subckt|model)\s+(\S+)", texts[str(lib)], re.I | re.M)}
        others = sorted({t for t in MPN_TOKEN.findall(blk.upper()) if re.search(r"\d", t) and re.search(r"[A-Z]", t)
                         and _near(t, bom) and t not in defined})  # a sibling model of the file is not a source
        if others:
            f.append({"sev": "warn", "code": "model-mpn",
                      "msg": f"{ref}: the comments of model {name} ({lib}) name {', '.join(others)}, the BOM part is "
                             f"{mpn}: a parameter taken from another maker's or variant's datasheet does not hold for "
                             f"the bought part. Set spice.mpn to the part the numbers come from"})
    return f


# ----------------------------------------------------------------------------------------------- mains and safety

OC_SYMBOL = re.compile(r"fuse|polyfuse|breaker|\bptc\b", re.I)
OC_TEXT = re.compile(r"\bfuse\b|^\s*[TF]\s?\d+(\.\d+)?\s*m?A\b|polyfuse|\bPPTC\b|breaker", re.I)
CONN_SYMBOL = re.compile(r"^Connector|terminal|screw_terminal|TerminalBlock", re.I)
MECH_NAME = re.compile(r"cover|rivet|screw|standoff|boss|fixing|mount|wall|frame|clip", re.I)


def is_overcurrent(ref: str, c) -> bool:
    if str(c.get("role") or "").lower() in ("fuse", "overcurrent"):
        return True
    k = _kind(ref)
    if k == "F":
        return True
    lim = c.get("limits") if isinstance(c.get("limits"), dict) else {}
    if c.get("current_rating_a") is not None or (lim.get("current_a") is not None and k not in ("K", "J", "U", "X")):
        return True
    return bool(OC_SYMBOL.search(str(c.get("symbol") or "").split(":")[-1])
                or OC_TEXT.search(str(c.get("value") or "")))


def is_connector(ref: str, c) -> bool:
    return _kind(ref) in ("J", "X", "P", "CN", "TB", "XS", "XP") or bool(CONN_SYMBOL.search(str(c.get("symbol") or "")))


def mains_nets(d) -> tuple[list[str], list[str]]:
    """(mains nets, declared input nets)."""
    names = sorted(set(d.nets()) | set(d.raw.get("nets") or {}))
    pats, inputs = [], []
    for r in ((d.raw.get("floorplan") or {}).get("regions") or []):
        if isinstance(r, dict) and r.get("kind") == "mains":
            v = r.get("nets") or []
            pats += [v] if isinstance(v, str) else list(v)
    for g in d.board.get("isolation") or []:
        if not isinstance(g, dict):
            continue
        if str(g.get("role") or "").lower() == "mains" or "mains" in f"{g.get('name', '')} {g.get('class', '')}".lower():
            v = g.get("nets") or []
            pats += [v] if isinstance(v, str) else list(v)
            inputs += [str(x) for x in g.get("inputs") or []]
    nets = [n for n in names if any(fnmatch.fnmatchcase(n, str(p)) for p in pats)]
    inputs += [n for n in names if str(d.net_info(n).get("role") or "").lower() == "mains_input"]
    return nets, sorted(set(inputs))


def mains_fuse_findings(d) -> list[dict]:
    mains, inputs = mains_nets(d)
    if not mains:
        return []
    nets = d.nets()
    comps = d.components
    oc = {r for r, c in comps.items() if is_overcurrent(r, c) and not c.get("virtual") and not c.get("dnp")}
    f = []
    waived = [n for n in mains if d.net_info(n).get("overcurrent")]
    if not any(r in oc for n in mains for r, _p in nets.get(n, [])):
        if waived:
            up = "; ".join(f"{n}: {d.net_info(n).get('overcurrent')}" for n in waived)
            return [{"sev": "info", "code": "mains-fuse", "msg": f"mains overcurrent protection outside the board: {up}"}]
        return [{"sev": "error", "code": "mains-fuse",
                 "msg": f"no overcurrent device (fuse, PTC, breaker) on the mains nets {', '.join(mains[:8])}: a short "
                        f"downstream sends the supply's prospective fault current (the installation breaker's "
                        f"let-through) through the board copper. Add a fuse in series with the line input "
                        f"(architecture: rating, breaking capacity, I2t against the copper), or name the upstream "
                        f"device in nets.<input>.overcurrent"}]

    def others(n):
        return sorted({r for r, _p in nets.get(n, []) if r not in oc and not is_connector(r, comps[r])})

    def fuse_first(n):
        return any(r in oc for r, _p in nets.get(n, [])) and not others(n)

    if inputs:
        for n in inputs:
            if n in waived or fuse_first(n):
                continue
            f.append({"sev": "error", "code": "mains-fuse",
                      "msg": f"mains input {n} reaches {', '.join(others(n)[:6]) or 'the circuit'} without an "
                             f"overcurrent device in series: put the fuse first after the terminal"})
        return f
    entry = [n for n in mains if any(is_connector(r, comps[r]) for r, _p in nets.get(n, []))]
    if entry and not any(fuse_first(n) or n in waived for n in entry):
        f.append({"sev": "error", "code": "mains-fuse",
                  "msg": "no mains input conductor goes through an overcurrent device first: "
                         + "; ".join(f"{n} connects {', '.join(others(n)[:4])}" for n in entry[:4])
                         + ". Mark the line input (nets.<n>.role: mains_input) and put the fuse first after the "
                           "terminal"})
    return f


def mechanical_findings(d) -> list[dict]:
    """Stage-2 mechanical items of a design with mains: a fixing, a cover, and touchability stated."""
    mains, _ = mains_nets(d)
    regs = [r for r in ((d.raw.get("floorplan") or {}).get("regions") or []) if isinstance(r, dict)]
    if not mains or not (d.raw.get("floorplan") or {}):
        return []
    mech = {str(r.get("mechanical")) for r in regs if r.get("mechanical")}
    msgs = []
    if not mech & {"fixing", "screw", "rivet", "standoff", "post", "frame"}:
        msgs.append("no fixing point (a region with mechanical: fixing | screw | rivet | standoff | frame): say how "
                    "the board is held, and keep the fixing's creepage to the mains copper")
    if not mech & {"cover", "wall", "enclosure", "film"}:
        msgs.append("no cover or barrier over the mains zone (mechanical: cover | wall | enclosure | film): the "
                    "touch protection is not in the design data")
    vague = [str(r.get("name")) for r in regs if MECH_NAME.search(str(r.get("name") or "")) and "mechanical" not in r
             and "accessible" not in r and not r.get("conductive")]
    if vague:
        msgs.append(f"regions {', '.join(vague[:6])} look like mechanical parts but do not say whether they are "
                    f"touchable: set mechanical: <kind> (fixings and walls count as accessible) or accessible: "
                    f"true | false with the reason")
    return [{"sev": "warn", "code": "mechanical", "msg": m} for m in msgs]


# ------------------------------------------------------------------------------------------------------- inputs


def inputs(d) -> list[str]:
    """Project files these checks read besides design.yaml (the check stage's cache key hashes them)."""
    out = ["notes/cost.md", "cost.md"]
    for r in requirements(d):
        out += [x for x in named_files(r) if x not in out]
    return out


def facts(d) -> dict:
    """What these checks read from requirement prose (the stage key keeps only the stripped requirements): the cost
    limits and the files each requirement names, with its priority."""
    reqs = requirements(d)
    return {"cost": cost_limits(reqs), "files": {str(r.get("id")): [str(r.get("priority")), named_files(r)]
                                                  for r in reqs if named_files(r)}}


def findings(d, libs=None) -> list[dict]:
    f = footprint_link_findings(d, libs)
    f += mains_fuse_findings(d)
    f += mechanical_findings(d)
    f += model_mpn_findings(d)
    f += cost_findings(d)
    f += req_file_findings(d)
    return f
