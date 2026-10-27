"""Reference-design retrieval (`hwflow ref search`, docs/reference-reuse.md).

An index over three sources, read at call time (no cache file):
- local designs: every `<dir>/<name>/design.yaml` under `settings.paths.ref_dirs` (examples/ and projects/), with
  the build verdict from its `build/REPORT.md` when one exists;
- the layout block library (`blocks/`, a project's own `blocks/`): pre-laid-out sub-circuits a design can bind;
- an external open-hardware index (`settings.paths.oshw_index`, JSON, optional): entries with `name`, `url`,
  `license`, `mcu`, `blocks` or `functions`, `mpns`, `layers`, `size`, and optionally a `parts` netlist
  ({ref: {value, mpn, footprint, pins: {pin: net}}}) so the sub-circuit around an MPN can be shown too.

A query is MPNs and/or function words. A hit is one part of one design (the anchor) with its sub-circuit: the parts on
its nets (the decoupling caps it owns, the passives and small parts on its signal nets), their values and nets, the
source and the licence, and the datasheet notes the design already has for that part. Nothing here calls the network.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .settings import get_settings

SCHEMA = "hwflow.ref/1"

# function words -> words that mark them in a symbol library, symbol, footprint, group, value or net name
SYNONYMS: dict[str, tuple[str, ...]] = {
    "ldo": ("ldo", "regulator_linear"),
    "regulator": ("regulator", "ldo", "buck", "boost"),
    "buck": ("buck", "step-down", "stepdown"),
    "boost": ("boost", "step-up", "stepup"),
    "charger": ("charger", "battery_management", "charge"),
    "relay": ("relay",),
    "usb": ("usb", "vbus", "cc1", "cc2"),
    "usbc": ("usb_c", "usb-c", "cc1", "cc2"),
    "mcu": ("mcu", "mcu_", "rf_module", "microcontroller", "esp32", "stm32", "rp2040"),
    "adc": ("adc", "analog_adc"),
    "dac": ("dac", "analog_dac"),
    "opamp": ("amplifier_operational", "opamp", "op-amp"),
    "amplifier": ("amplifier", "amp"),
    "ntc": ("ntc", "thermistor"),
    "temperature": ("temp", "ntc", "thermistor", "sensor_temperature"),
    "mosfet": ("transistor_fet", "mosfet", "fet"),
    "hbridge": ("driver_motor", "h-bridge", "hbridge", "drv88"),
    "motor": ("driver_motor", "motor"),
    "crystal": ("crystal", "xtal", "oscillator"),
    "esd": ("esd", "tvs", "power_protection"),
    "tvs": ("tvs", "esd", "power_protection"),
    "fuse": ("fuse", "polyfuse"),
    "battery": ("battery", "bat", "vbat"),
    "led": ("led",),
    "audio": ("audio", "headphone", "dac"),
    "headphone": ("headphone", "audio", "amplifier_audio"),
    "i2c": ("sda", "scl", "i2c"),
    "spi": ("sck", "mosi", "miso", "spi"),
    "uart": ("uart", "txd", "rxd"),
    "mains": ("mains", "ac_", "230v", "relay"),
}


def norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _is_mpn(tok: str) -> bool:
    n = norm(tok)
    return len(n) >= 4 and any(ch.isdigit() for ch in n) and any(ch.isalpha() for ch in n)


@dataclass
class RefDesign:
    kind: str                     # example | project | block | oshw
    name: str
    source: str                   # path relative to the repository, or URL
    license: str = ""
    verified: str = ""            # build verdict, block check, or "" when unknown
    description: str = ""
    functions: list[str] = field(default_factory=list)
    mpns: list[str] = field(default_factory=list)      # oshw entries without a netlist
    mcu: str = ""
    layers: int | None = None
    size: str = ""
    parts: dict[str, dict] = field(default_factory=dict)
    dir: Path | None = None       # local designs: the project folder (datasheet notes)
    ground: set[str] = field(default_factory=set)

    def nets(self) -> dict[str, list[tuple[str, str]]]:
        out: dict[str, list[tuple[str, str]]] = {}
        for ref, c in self.parts.items():
            for pin, net in _pins(c).items():
                if net.upper() not in ("NC", "~", ""):
                    out.setdefault(net, []).append((ref, pin))
        return out


def _pins(c: dict) -> dict[str, str]:
    return {str(k): str(v) for k, v in ((c or {}).get("pins") or {}).items()}


def _rel(p: Path) -> str:
    root = Path(get_settings().paths.root)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)


def _verdict(proj: Path) -> str:
    """The build verdict of a local design (`build/REPORT.md` first line `Verdict:`), with its date."""
    rep = proj / "build" / "REPORT.md"
    if not rep.is_file():
        return ""
    text = rep.read_text(errors="replace")
    v = re.search(r"^Verdict: (.*)$", text, re.M)
    when = re.search(r"^Updated (\S+)", text, re.M)
    if not v:
        return ""
    return f"build {v.group(1)[:60]}" + (f" ({when.group(1)})" if when else "")


def _ground(raw: dict, parts: dict) -> set[str]:
    g = {n for n, i in (raw.get("nets") or {}).items() if (i or {}).get("class") == "ground"}
    if g:
        return g
    return {n for c in parts.values() for n in _pins(c).values() if n.upper() in ("GND", "0", "AGND", "DGND", "PGND")}


def load_local(dirs: list[Path] | None = None) -> list[RefDesign]:
    s = get_settings()
    dirs = dirs if dirs is not None else [Path(p) for p in s.paths.ref_dirs.split(os.pathsep) if p]
    out = []
    for base in dirs:
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*/design.yaml")):
            try:
                raw = yaml.safe_load(f.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            parts = {str(r): dict(c or {}) for r, c in (raw.get("components") or {}).items()}
            b = raw.get("board") or {}
            out.append(RefDesign(
                kind="example" if base.name == "examples" else "project", name=f.parent.name, source=_rel(f),
                license=str(raw.get("license") or "repository (internal)"), verified=_verdict(f.parent),
                description=" ".join(str(raw.get("description") or "").split()),
                layers=b.get("layers"), size=f"{b['width_mm']:g}x{b['height_mm']:g} mm"
                if b.get("width_mm") and b.get("height_mm") else "",
                parts=parts, dir=f.parent, ground=_ground(raw, parts)))
    return out


def load_blocks(design_dir: Path | None = None) -> list[RefDesign]:
    from . import blocks
    dirs = ([design_dir / "blocks"] if design_dir is not None and (design_dir / "blocks").is_dir() else []) \
        + [blocks.library_root()]
    out, seen = [], set()
    for d in dirs:
        for f in sorted(d.glob("*/block.yaml")):
            if f.parent.name in seen:
                continue
            seen.add(f.parent.name)
            try:
                b = yaml.safe_load(f.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            parts = {}
            for role, r in (b.get("roles") or {}).items():
                r = dict(r or {})
                ref = str(r.get("ref") or role)
                r["role"] = str(role)
                if not r.get("value"):
                    r["value"] = ">= " + str(r["value_min"]) if r.get("value_min") else str(r.get("value_max") or "")
                parts[ref] = r
            out.append(RefDesign(
                kind="block", name=str(b.get("name") or f.parent.name), source=_rel(f),
                license="repository (internal)",
                verified=f"layout block: placement and copper ready (`blocks: {{x: {{use: {b.get('name') or f.parent.name}}}}}`)",
                description=" ".join(str(b.get("description") or "").split()),
                layers=b.get("layers_min"), parts=parts, ground={"GND"} & {n for c in parts.values() for n in _pins(c).values()}))
    return out


def _strs(v) -> list[str]:
    """A list of names from strings or dicts ({name|function|mpn|description: ...})."""
    if v is None:
        return []
    if isinstance(v, (str, int, float)):
        return [str(v)]
    if isinstance(v, dict):
        return [str(k) for k in v]
    out = []
    for x in v:
        if isinstance(x, dict):
            for k in ("mpn", "name", "function", "value", "description"):
                if x.get(k):
                    out.append(str(x[k]))
                    break
        elif x is not None:
            out.append(str(x))
    return out


def _size(v) -> str:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        try:
            return f"{float(v[0]):g}x{float(v[1]):g} mm"
        except (TypeError, ValueError):
            return ""
    if isinstance(v, dict):
        w, h = v.get("width_mm") or v.get("w"), v.get("height_mm") or v.get("h")
        return _size([w, h]) if w and h else ""
    return str(v or "")


def load_oshw(path: Path | None = None) -> tuple[list[RefDesign], str | None]:
    """The external index: (designs, None), or ([], reason) when it is missing or unreadable. Accepts a list of
    entries or an object holding one under `designs`, `boards`, `entries` or `items`."""
    p = Path(path) if path is not None else Path(get_settings().paths.oshw_index)
    if not p.is_file():
        return [], f"no external index at {_rel(p)}"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        return [], f"external index {_rel(p)} unreadable: {e}"
    if isinstance(data, dict):
        data = next((data[k] for k in ("designs", "boards", "entries", "items") if isinstance(data.get(k), list)), [])
    out = []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        parts = {}
        net = e.get("parts") if isinstance(e.get("parts"), dict) else e.get("netlist")
        if isinstance(net, dict):
            parts = {str(r): dict(c) for r, c in net.items() if isinstance(c, dict)}
        mpns = _strs(e.get("mpns")) + ([] if parts else _strs(e.get("parts")))
        mcu = e.get("mcu")
        layers = e.get("layers")
        try:
            layers = int(layers) if layers is not None else None
        except (TypeError, ValueError):
            layers = None
        out.append(RefDesign(
            kind="oshw", name=str(e["name"]), source=str(e.get("url") or e.get("repo") or ""),
            license=str(e.get("license") or "unknown (check before reuse)"),
            verified=str(e.get("status") or "external, unverified"),
            description=" ".join(str(e.get("description") or "").split()),
            functions=_strs(e.get("blocks")) + _strs(e.get("functions")), mpns=mpns,
            mcu=", ".join(_strs(mcu)), layers=layers, size=_size(e.get("size") or e.get("size_mm")), parts=parts,
            ground={n for c in parts.values() for n in _pins(c).values() if n.upper() in ("GND", "0", "AGND", "DGND")}))
    return out, None


# ------------------------------------------------------------------ matching

def _words(c: dict, ref: str) -> tuple[str, str]:
    """The lower-case text a function word is looked up in: (symbol, footprint, group, value, description, role;
    the part's net names). A hit in the first counts more than one on a net name."""
    fields = [c.get("symbol"), c.get("footprint"), c.get("group"), c.get("value"), c.get("mpn"), c.get("description"),
              c.get("role")]
    return " ".join(str(x) for x in fields if x).lower(), " ".join(_pins(c).values()).lower()


def _mpn_score(tok: str, c: dict) -> int:
    t = norm(tok)
    best = 0
    for k in ("mpn", "value", "lcsc", "symbol"):
        v = c.get(k)
        if not v:
            continue
        n = norm(str(v).split(":")[-1]) if k == "symbol" else norm(v)
        if not n:
            continue
        if n == t:
            best = max(best, 10)
        elif len(t) >= 4 and (n.startswith(t) or t.startswith(n) and len(n) >= 5):
            best = max(best, 7)
        elif len(t) >= 5 and t in n:
            best = max(best, 5)
    return best


def _word_score(tok: str, text: str | tuple[str, str]) -> int:
    keys = SYNONYMS.get(tok.lower(), ()) + (tok.lower(),)
    if isinstance(text, tuple):
        return 3 if any(k in text[0] for k in keys) else 1 if any(k in text[1] for k in keys) else 0
    return 2 if any(k in text for k in keys) else 0


def _subcircuit(d: RefDesign, anchor: str, max_rail: int = 8) -> list[dict]:
    """The parts around `anchor`: on each of its non-ground nets, every part when the net is small, and on a big rail
    (more than `max_rail` pads) the capacitors that decouple the anchor (`decouples:`, else none). Ground-only
    neighbours are left out."""
    nets = d.nets()
    a = d.parts[anchor]
    out: dict[str, dict] = {}
    for pin, net in _pins(a).items():
        if net in d.ground or net.upper() in ("NC", "~", ""):
            continue
        conns = [(r, p) for r, p in nets.get(net, []) if r != anchor]
        big = len(conns) + 1 > max_rail
        for r, _p in conns:
            c = d.parts[r]
            if big and str(c.get("decouples") or "") != anchor:
                continue
            if not big and c.get("decouples") and str(c.get("decouples")) != anchor:
                continue
            e = out.setdefault(r, {"ref": r, "value": str(c.get("value") or ""), "mpn": str(c.get("mpn") or ""),
                                   "footprint": str(c.get("footprint") or "").split(":")[-1],
                                   "nets": sorted(set(_pins(c).values()), key=str)})
            if c.get("role"):
                e["role"] = str(c["role"])
            e.setdefault("via", [])
            if f"{anchor}.{pin}" not in e["via"]:
                e["via"].append(f"{anchor}.{pin}")
    return sorted(out.values(), key=lambda e: (_kind_order(e["ref"]), e["ref"]))


DISCRETE = {"R", "C", "L", "D", "Y", "X", "F", "FB", "M", "Q", "RN", "RV", "TH", "NTC", "SW", "K"}
NOT_CIRCUIT = {"TP", "MP", "H", "FID", "LOGO"}


def split_neighbours(sub: list[dict], max_n: int = 24) -> tuple[list[dict], list[str], int]:
    """(the discrete parts, at most `max_n`; the ICs and connectors as `ref value`; how many were cut).
    Test points and mechanical parts are dropped."""
    disc, peers = [], []
    for e in sub:
        k = re.match(r"[A-Za-z]*", e["ref"]).group(0).upper()
        if k in NOT_CIRCUIT:
            continue
        if k in DISCRETE:
            disc.append(e)
        else:
            peers.append(f"{e['ref']} {e['value']}")
    return disc[:max_n], peers, max(0, len(disc) - max_n)


def _kind_order(ref: str) -> int:
    k = re.match(r"[A-Za-z]*", ref).group(0).upper()
    return {"C": 2, "R": 1, "L": 0}.get(k, 3)


def _notes(d: RefDesign, c: dict) -> list[str]:
    """Datasheet notes the design (or the shared library) already holds for this part."""
    m = norm(c.get("mpn") or c.get("value"))
    if len(m) < 4:
        return []
    out = []
    dirs = ([d.dir / "notes" / "datasheets"] if d.dir else []) + [Path(get_settings().paths.library_dir) / "datasheets"]
    for nd in dirs:
        if not nd.is_dir():
            continue
        for f in sorted(nd.glob("*.md")):
            s = norm(f.stem)
            if len(s) >= 4 and (m.startswith(s) or s.startswith(m)):
                out.append(_rel(f))
    return out


def search(query: list[str], *, limit: int = 5, designs: list[RefDesign] | None = None,
           include_blocks: bool = True, oshw_path: Path | None = None, design_dir: Path | None = None) -> dict:
    """Ranked hits for `query` (words). Returns {schema, query, hits, sources}; see module doc."""
    toks = [t for t in (w.strip() for q in query for w in q.split()) if t]
    sources: dict = {}
    if designs is None:
        designs = load_local()
        sources["local"] = len(designs)
        if include_blocks:
            bl = load_blocks(design_dir)
            sources["blocks"] = len(bl)
            designs = designs + bl
        ext, why = load_oshw(oshw_path)
        sources["oshw"] = len(ext) if why is None else why
        designs = designs + ext
    mpn_toks = [t for t in toks if _is_mpn(t)]
    word_toks = [t for t in toks if not _is_mpn(t)]
    raw_hits = []
    for d in designs:
        dtext = " ".join([d.name, d.description, " ".join(d.functions), d.mcu]).lower()
        dword = sum(_word_score(t, dtext) for t in word_toks)
        for ref, c in d.parts.items():
            ms = [_mpn_score(t, c) for t in mpn_toks]
            if mpn_toks and not any(ms):
                continue
            text = _words(c, ref)
            ws = [_word_score(t, text) for t in word_toks]
            if not mpn_toks and not any(ws):
                continue
            if _kind_order(ref) < 3 and not mpn_toks and len(_pins(c)) <= 2:
                continue  # a function word on a passive (a cap in group power) is not an anchor
            matched = sum(1 for x in ms if x) + sum(1 for x in ws if x)
            score = sum(ms) + sum(ws) + dword // 2 + (1 if d.verified.startswith(("build PASS", "layout block")) else 0)
            raw_hits.append((matched, score, d, ref))
        if not d.parts:  # an external entry without a netlist: match its MPN list and functions
            ms = [max((10 if norm(m) == norm(t) else 7 if norm(m).startswith(norm(t)) else 0) for m in d.mpns)
                  if d.mpns else 0 for t in mpn_toks]
            if (mpn_toks and any(ms)) or (not mpn_toks and dword):
                matched = sum(1 for x in ms if x) + sum(1 for t in word_toks if _word_score(t, dtext))
                raw_hits.append((matched, sum(ms) + dword, d, None))
    raw_hits.sort(key=lambda h: (-h[0], -h[1], h[2].kind != "block", h[2].name, h[3] or ""))
    hits, seen, per_design = [], {}, {}
    for _matched, score, d, ref in raw_hits:
        if per_design.get(d.name, 0) >= 2:  # at most two anchors of one design: room for other designs
            continue
        if ref is None:
            hit = {"design": d.name, "kind": d.kind, "source": d.source, "license": d.license,
                   "verified": d.verified, "score": score, "mpns": d.mpns[:20], "functions": d.functions[:20],
                   "mcu": d.mcu, "layers": d.layers, "size": d.size}
            hits.append(hit)
            continue
        c = d.parts[ref]
        sub = _subcircuit(d, ref)
        sig = (norm(c.get("mpn") or c.get("value")), tuple(sorted((norm(e["value"]), e["footprint"]) for e in sub)))
        if sig in seen:  # the same sub-circuit in another design (project revisions): one line
            seen[sig].setdefault("also_in", []).append(d.name)
            continue
        disc, peers, cut = split_neighbours(sub)
        hit = {"design": d.name, "kind": d.kind, "source": d.source, "license": d.license, "verified": d.verified,
               "score": score,
               "anchor": {"ref": ref, "value": str(c.get("value") or ""), "mpn": str(c.get("mpn") or ""),
                          "symbol": str(c.get("symbol") or ""), "footprint": str(c.get("footprint") or ""),
                          "pins": _pins(c)},
               "neighbours": disc}
        if peers:
            hit["peers"] = peers
        if cut:
            hit["more_neighbours"] = cut
        if d.kind == "block":
            hit["block"] = d.name
        if d.layers:
            hit["layers"] = d.layers
        notes = _notes(d, c)
        if notes:
            hit["notes"] = notes
        seen[sig] = hit
        per_design[d.name] = per_design.get(d.name, 0) + 1
        hits.append(hit)
        if len(hits) >= limit:
            break
    return {"schema": SCHEMA, "query": toks, "hits": hits[:limit], "sources": sources}


def text(res: dict) -> str:
    """Agent-friendly text of a search result."""
    src = res.get("sources") or {}
    L = [f"ref search {' '.join(res['query'])}: {len(res['hits'])} hits (local designs {src.get('local', '-')}, "
         f"blocks {src.get('blocks', '-')}, external {src.get('oshw', '-')})"]
    for h in res["hits"]:
        head = f"- {h['design']} [{h['kind']}] {h['source']}; licence {h['license']}"
        if h.get("verified"):
            head += f"; {h['verified']}"
        L.append(head)
        if h.get("also_in"):
            L.append(f"  same sub-circuit also in: {', '.join(h['also_in'])}")
        if "anchor" not in h:
            extra = [f"MCU {h['mcu']}" if h.get("mcu") else "", f"{h['layers']} layers" if h.get("layers") else "",
                     h.get("size") or ""]
            if h.get("functions"):
                extra.append("functions: " + ", ".join(h["functions"][:8]))
            if h.get("mpns"):
                extra.append("MPNs: " + ", ".join(h["mpns"][:10]))
            L.append("  " + "; ".join(x for x in extra if x))
            continue
        a = h["anchor"]
        L.append(f"  {a['ref']} {a['value']}" + (f" ({a['mpn']})" if a["mpn"] and a["mpn"] != a["value"] else "")
                 + f" {a['symbol']} {a['footprint'].split(':')[-1]}")
        L.append("    pins: " + " ".join(f"{p}={n}" for p, n in sorted(a["pins"].items(), key=lambda x: _nat(x[0]))))
        for e in h["neighbours"]:
            L.append(f"    {e['ref']:<5} {e['value']:<14} {e['footprint']:<24} nets {', '.join(e['nets'])}"
                     + (f"  [{e['mpn']}]" if e["mpn"] and e["mpn"] != e["value"] else ""))
        if h.get("more_neighbours"):
            L.append(f"    ... {h['more_neighbours']} more discrete parts")
        if h.get("peers"):
            L.append(f"    ICs and connectors on its nets: {', '.join(h['peers'][:12])}"
                     + (f" and {len(h['peers']) - 12} more" if len(h["peers"]) > 12 else ""))
        if h.get("block"):
            L.append(f"  bind it: blocks: {{<name>: {{use: {h['block']}, parts: {{<role>: <ref>, ...}}}}}} "
                     f"(`hwflow block show {h['block']}`)")
        for n in h.get("notes") or []:
            L.append(f"  datasheet notes: {n}")
    if not res["hits"]:
        L.append("no match: search by the MPN family (e.g. MAX17227 for MAX17227AATA+T) or by function words "
                 "(boost, ldo, relay, ntc, usbc, hbridge)")
    return "\n".join(L)


def _nat(s: str):
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", s)]
