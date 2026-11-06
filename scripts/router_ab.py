"""Router A/B metrics from scored eval runs (krt vs freerouting). Deterministic, no builds.

  router_ab.py project <proj_dir>
  router_ab.py collect <run_dir>... --label NAME --out FILE.json
  router_ab.py table FILE.json [FILE.json ...]

Run dir layout (evals/harness.py): <run_dir>/<fixture>__<model>__t<N>/{score.json, proj/build/{status.json,*.kicad_pcb}}.
Layout time: status.json timing.stages.layout.s (stage wall time, incl. DRC); fallback layout.seconds.
"""
import argparse, json, math, re, statistics, sys
from collections import defaultdict
from pathlib import Path

NUM = r"(-?[\d.]+)"
SEG = re.compile(r"\(segment\s+\(start " + NUM + r"\s+" + NUM + r"\)\s*\(end " + NUM + r"\s+" + NUM + r"\)")
ARC = re.compile(r"\(arc\s+\(start " + NUM + r"\s+" + NUM + r"\)\s*\(mid " + NUM + r"\s+" + NUM + r"\)\s*\(end " + NUM + r"\s+" + NUM + r"\)")
VIA = re.compile(r"\(via[\s(]")


def _arc_len(x1, y1, xm, ym, x2, y2):
    d = 2 * (x1 * (ym - y2) + xm * (y2 - y1) + x2 * (y1 - ym))
    if abs(d) < 1e-12:
        return math.dist((x1, y1), (x2, y2))
    ux = ((x1**2 + y1**2) * (ym - y2) + (xm**2 + ym**2) * (y2 - y1) + (x2**2 + y2**2) * (y1 - ym)) / d
    uy = ((x1**2 + y1**2) * (x2 - xm) + (xm**2 + ym**2) * (x1 - x2) + (x2**2 + y2**2) * (xm - x1)) / d
    r = math.dist((ux, uy), (x1, y1))
    a1, am, a2 = (math.atan2(y - uy, x - ux) for x, y in ((x1, y1), (xm, ym), (x2, y2)))
    tw = lambda a, b: (b - a) % (2 * math.pi)
    ang = tw(a1, a2) if tw(a1, am) <= tw(a1, a2) else 2 * math.pi - tw(a1, a2)
    return r * ang


def pcb_metrics(pcb: Path) -> dict:
    t = pcb.read_text(errors="replace")
    segs = [tuple(map(float, m)) for m in SEG.findall(t)]
    arcs = [tuple(map(float, m)) for m in ARC.findall(t)]
    mm = sum(math.dist((a, b), (c, d)) for a, b, c, d in segs) + sum(_arc_len(*a) for a in arcs)
    return {"segments": len(segs) + len(arcs), "vias": len(VIA.findall(t)), "track_mm": round(mm, 2)}


def project_metrics(proj: Path) -> dict:
    b = proj / "build"
    row = {"router_used": None, "unrouted": None, "drc_errors": None, "post_pass": None, "layout_s": None}
    sp = b / "status.json"
    if sp.exists():
        st = json.loads(sp.read_text())
        lay = st.get("layout") or {}
        row.update(router_used=lay.get("router_used"), unrouted=lay.get("unrouted"), drc_errors=lay.get("drc_errors"))
        row["post_pass"] = (st.get("score") or {}).get("post_pass_ratio")
        row["layout_s"] = ((st.get("timing") or {}).get("stages", {}).get("layout") or {}).get("s", lay.get("seconds"))
    pcbs = sorted(b.glob("*.kicad_pcb"))
    row.update(pcb_metrics(pcbs[0]) if pcbs else {"segments": None, "vias": None, "track_mm": None})
    row["missing"] = not (sp.exists() and pcbs)
    return row


def collect(run_dirs, label):
    rows = []
    for rd in map(Path, run_dirs):
        for tdir in sorted(p for p in rd.glob("*__*__t*") if p.is_dir()):
            fx, _model, tr = tdir.name.split("__")
            r = project_metrics(tdir / "proj")
            card = json.loads((tdir / "score.json").read_text()) if (tdir / "score.json").exists() else {}
            if card.get("m_post_pass_ratio") is not None:
                r["post_pass"] = card["m_post_pass_ratio"]
            if r["unrouted"] is None:
                r["unrouted"] = card.get("m_unrouted")
            if r["drc_errors"] is None:
                r["drc_errors"] = card.get("m_drc_errors")
            r.update(fixture=fx, trial=int(tr.lstrip("t")), label=label, score=card.get("score"),
                     timeout=bool(card.get("build_timeout")) or r["missing"] or not card, run=str(rd))
            rows.append(r)
    return rows


METRICS = ["unrouted", "drc_errors", "post_pass", "score", "segments", "vias", "track_mm", "layout_s"]


def table(files):
    rows = [r for f in files for r in json.loads(Path(f).read_text())]
    g = defaultdict(list)
    for r in rows:
        g[(r["label"], r["fixture"])].append(r)
    print("| label | fixture | n | timeouts | " + " | ".join(METRICS) + " |")
    print("|" + "---|" * (4 + len(METRICS)))
    for (lab, fx), rs in sorted(g.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        cells = []
        for m in METRICS:
            v = [r[m] for r in rs if r.get(m) is not None]
            cells.append("-" if not v else f"{statistics.median(v):.4g} [{min(v):.4g}-{max(v):.4g}]")
        print(f"| {lab} | {fx} | {len(rs)} | {sum(bool(r.get('timeout')) for r in rs)} | " + " | ".join(cells) + " |")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("project").add_argument("proj")
    c = sub.add_parser("collect")
    c.add_argument("run_dirs", nargs="+")
    c.add_argument("--label", required=True)
    c.add_argument("--out", required=True)
    sub.add_parser("table").add_argument("files", nargs="+")
    a = ap.parse_args()
    if a.cmd == "project":
        print(json.dumps(project_metrics(Path(a.proj))))
    elif a.cmd == "collect":
        rows = collect(a.run_dirs, a.label)
        Path(a.out).write_text(json.dumps(rows, indent=1))
        print(f"{len(rows)} rows -> {a.out}", file=sys.stderr)
    else:
        table(a.files)


if __name__ == "__main__":
    main()
