"""hwflow command line. Every command prints a short, agent-friendly summary."""
# Only argparse and dispatch live here; the stages, status, report, trace and the build loop are in hwflow.pipeline.

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from .sim import spice
from .commands.block import cmd_block
from .commands.em import cmd_em
from .core.design import Design
from .core.libs import Libs
from .pipeline.hooks import registry
from .pipeline.report import _fmt, write_report
from .pipeline.runner import BuildOptions, run_build
from .pipeline.status import _design_hash, _fresh_status, _out, _save_status
from .pipeline.trace import trace


def _load(proj: str) -> Design:
    return Design.load(proj)


def cmd_lib(a) -> int:
    libs = Libs()
    q = " ".join(a.query)
    if a.what == "sym":
        for lid, desc in libs.search_symbols(q):
            print(f"{lid}  {desc}")
    elif a.what == "fp":
        print("\n".join(libs.search_footprints(q)))
    elif a.what == "show":
        s = libs.symbol(q)
        print(f"{q} units={s['units']} footprint={s['props'].get('Footprint', '')}")
        print(f"  {s['props'].get('Description', '')}")

        def natural(num):
            return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", num)]
        for p in sorted(s["pins"], key=lambda p: (p["unit"], natural(p["number"]))):
            print(f"  pin {p['number']:>4} {p['name']:<12} {p['type']:<14} unit {p['unit']}{' hidden' if p['hidden'] else ''}")
    else:
        fp = libs.footprint(q)
        print(f"{q} bbox={tuple(round(v, 2) for v in fp['bbox'])}")
        for p in fp["pads"]:
            print(f"  pad {p['number']:>4} {p['kind']:<10} {p['shape']:<9} at ({p['x']:.2f},{p['y']:.2f}) {p['w']}x{p['h']}")
    return 0


def cmd_parts(a) -> int:
    from .core import parts
    filters = dict(f.split("=", 1) for f in a.filters)
    for r in parts.search(a.category, filters, a.text, a.limit):
        print(f"{r['lcsc']:>10} {r['mpn']:<28} {r['package']:<14} stock={r['stock']:<9} ${_fmt(r['price_usd'])}"
              f"{' basic' if r['basic'] else ''}  {r['desc']}")
    return 0


def cmd_nets(d: Design, a) -> int:
    import fnmatch
    nst = (_fresh_status(d).get("layout") or {}).get("nets")
    if nst is None:
        print("no routed nets in build/status.json: run the layout stage first")
        return 1
    print(f"{'net':<16} {'class w':>7} {'min w':>6} {'max w':>6} {'length':>8} {'narrow':>7} {'vias':>4}  layers")
    for n, r in nst.items():
        if a.net and not any(fnmatch.fnmatchcase(n, g) for g in a.net):
            continue
        f = lambda v: "-" if v is None else f"{v:g}"  # noqa: E731
        print(f"{n:<16} {f(r.get('width')):>7} {f(r['min_w']):>6} {f(r['max_w']):>6} {r['length_mm']:>8.2f} "
              f"{r['narrow_mm']:>7.2f} {r['vias']:>4}  {' '.join(r['layers'])}")
    return 0


def cmd_trace(d: Design) -> int:
    t = trace(d)
    for r in t["rows"]:
        print(f"{r['id']:<5} {r['status']:<10} {r['priority']:<6} {r['text'][:70]}"
              + (f"  [{', '.join(r['measures'])}]" if r["measures"] else ""))
    for r in t["rows"]:
        if r.get("evidence_missing"):
            print(f"{r['id']:<5} evidence file missing or empty: {', '.join(r['evidence_missing'])}")
    if t["evidence_only"]:
        print(f"layout requirements verified by evidence text only, no board check (layout?): "
              f"{', '.join(t['evidence_only'])}; tie them to a check with `req:` (nets, board.isolation, budgets)")
    print(f"coverage {t['covered']}/{t['total']} verified, {t['failing']} failing, {t['unverified']} unverified"
          + (f"; evidence files missing for {len(t['evidence_missing'])} ({', '.join(t['evidence_missing'])})"
             if t["evidence_missing"] else ""))
    _save_status(d, "trace", {k: v for k, v in t.items() if k != "rows"})
    return 0


def cmd_optimize(d: Design, a) -> int:
    from .sim import optimize
    res = optimize.run(d, _out(d), a.budget)
    print(json.dumps({k: res[k] for k in ("evals", "score", "fails", "start", "best")}, indent=1))
    print("measures:", {k: _fmt(v) for k, v in res["measures"].items()})
    if a.apply:
        optimize.apply(d, res)
        print("applied to design.yaml")
    _save_status(d, "optimize", {k: res[k] for k in ("evals", "score", "fails", "start", "best", "measures")})
    return 0


def cmd_fw_test(d: Design, a) -> int:
    from .fw.hosttest import host_test
    rc, text, summary = host_test(d, Libs(design=d), _out(d))
    print(text)
    _save_status(d, "fw_test", {**summary, "design_hash": _design_hash(d), "at": time.time()})
    return rc


def cmd_fw(d: Design, a) -> int:
    from . import fw
    path, findings = fw.run(d, Libs(design=d), _out(d))
    if a.scaffold:
        for f in fw.scaffold(d, d.dir / "fw"):
            print(f"scaffold {f}")
    for f in findings:
        print(f"{f['severity']:<8} {f['code']:<24} {f['message']}")
    nerr = sum(f["severity"] == "error" for f in findings)
    print(f"{path if path.exists() else 'no board.h'}: {nerr} errors, {len(findings) - nerr} warnings")
    _save_status(d, "fw", {"errors": nerr, "warnings": len(findings) - nerr, "findings": findings,
                           "design_hash": _design_hash(d), "at": time.time()})
    return 1 if nerr else 0


def cmd_floorplan(d: Design, a) -> int:
    from .layout.place import floorplan
    text, data = floorplan.run(d, Libs(design=d), _out(d), a.iters, a.seeds)
    _save_status(d, "floorplan", {**data, "design_hash": _design_hash(d), "at": time.time()})
    print(text)
    return 0 if data["ok"] else 1


def build_options(a) -> BuildOptions:
    """The stage run a command asks for: `build` runs the stages up to --until, the others run their own stage."""
    g = lambda k, dflt=None: getattr(a, k, dflt)  # noqa: E731
    return BuildOptions(
        until=g("until", "fab"), stages=None if a.cmd == "build" else [a.cmd], build=a.cmd == "build",
        mc=g("mc", 0), reuse=g("reuse", False), force=g("force", False), rebuild=g("rebuild", False),
        auto=g("auto", False), attempts=g("attempts", 4), retry_layout=g("retry_layout", False),
        workers=g("workers"), layout_search=a.cmd == "layout", seeds=g("seeds", 4), iters=g("iters", 30000),
        passes=g("passes", 30), brief=g("brief", False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hwflow", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    brief = "print only the report lines that changed since the last report, and the verdict"
    for name in ("check", "sch", "post", "fab", "report"):
        p = sub.add_parser(name); p.add_argument("project")
        if name != "report":
            p.add_argument("--brief", action="store_true", help=brief)
    p = sub.add_parser("sim"); p.add_argument("project"); p.add_argument("--mc", type=int, default=0)
    p.add_argument("--brief", action="store_true", help=brief)
    auto = ("placement, routing with net-order retries, area search and DRC in one call: up to --attempts "
            "placements in parallel (--workers, default cpu count - 1), stop at the first clean board or when the errors "
            "are on fixed parts; prints only the final numbers")
    p = sub.add_parser("layout"); p.add_argument("project"); p.add_argument("--reuse", action="store_true")
    p.add_argument("--brief", action="store_true", help=brief)
    p.add_argument("--auto", action="store_true", help=auto); p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--seeds", type=int, default=4); p.add_argument("--iters", type=int, default=30000)
    p.add_argument("--passes", type=int, default=30); p.add_argument("--workers", type=int, default=None)
    retry = ("run a layout the layout diagnosis blocked (same parts, nets and board as a build it sent back to an "
             "earlier stage); recorded in the layout history")
    p.add_argument("--retry-layout", action="store_true", help=retry)
    p = sub.add_parser("build"); p.add_argument("project"); p.add_argument("--mc", type=int, default=0)
    p.add_argument("--until", choices=["check", "sch", "sim", "layout", "post", "fab"], default="fab")
    p.add_argument("--reuse", action="store_true"); p.add_argument("--force", action="store_true",
                                                                   help="continue past failing stages")
    p.add_argument("--rebuild", action="store_true", help="run every stage even when its inputs are unchanged")
    p.add_argument("--auto", action="store_true", help="layout stage as in `layout --auto`")
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--retry-layout", action="store_true", help=retry)
    p.add_argument("--brief", action="store_true", help=brief)
    p.add_argument("--inline", action="store_true", help="run the stages in this process (the default)")
    p.add_argument("--remote", action="store_true",
                   help="run on the hosted platform (the `remote` executor plug-in, or HWFLOW_EXECUTOR=remote)")
    p.add_argument("--detach", action="store_true",
                   help="start the build in the background and print its run id; wait with `hwflow run wait`")
    p = sub.add_parser("run", help="status of a build run, or wait for it to finish (replaces watching a PID)")
    p.add_argument("what", choices=["status", "wait"]); p.add_argument("project")
    p.add_argument("run_id", nargs="?", help="default: the newest run of the project")
    p.add_argument("--timeout", type=float, default=580, help="wait: give up after this many seconds (exit 2)")
    p.add_argument("--remote", action="store_true", help="a run on the hosted platform")
    p = sub.add_parser("optimize"); p.add_argument("project"); p.add_argument("--budget", type=int)
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("lib"); p.add_argument("what", choices=["sym", "fp", "show", "pads"]); p.add_argument("query", nargs="+")
    p = sub.add_parser("block", help="layout blocks: list, show <name>, check <name>, extract <board> --refs ...")
    p.add_argument("what", choices=["list", "show", "check", "extract"]); p.add_argument("arg", nargs="?")
    p.add_argument("--refs", help="extract: parts of the block, comma separated"); p.add_argument("--name")
    p.add_argument("--source", help="extract: where the block comes from (board, run, datasheet)")
    p.add_argument("--roles", help="extract: role=ref pairs, comma separated (default: role = ref)")
    p.add_argument("--project", help="design whose blocks/, symbols and values to use")
    p.add_argument("--out", help="extract: library folder (default: the repository blocks/); check: work folder")
    p = sub.add_parser("parts"); p.add_argument("category"); p.add_argument("filters", nargs="*",
                                                                           help="key=value server filters")
    p.add_argument("--text"); p.add_argument("--limit", type=int, default=15)
    sub.add_parser("trace").add_argument("project")
    p = sub.add_parser("fw", help="firmware headers build/fw/board.h, events.h, modules.h from design.yaml and the "
                                  "HW/SW contract interface.yaml (docs/hwsw-codesign.md)")
    p.add_argument("target", nargs="+", metavar="[test] project",
                   help="`fw <proj>`: generate the headers; `fw test <proj>`: also build and run the firmware host "
                        "tests with gcc (exit 0 = pass)")
    p.add_argument("--scaffold", action="store_true", help="also write fw/ module skeletons, app wiring, Makefile and "
                                                           "a host-test stub (never overwrites)")
    p = sub.add_parser("nets", help="routed copper per net from the last layout: widths, length, narrow length, "
                                    "layers, vias")
    p.add_argument("project"); p.add_argument("net", nargs="*", help="net names or globs (default: all)")
    p = sub.add_parser("floorplan", help="early layout feasibility of the floorplan: field (area, depth, regions, quick "
                                         "placement and congestion); no simulation or routing")
    p.add_argument("project"); p.add_argument("--iters", type=int, default=20000); p.add_argument("--seeds", type=int, default=2)
    p = sub.add_parser("netlist"); p.add_argument("project"); p.add_argument("analysis", nargs="?")
    p = sub.add_parser("em", help="optional openEMS field-solver run on nets with `em:` set (prototype)")
    p.add_argument("project"); p.add_argument("--net", action="append")
    p.add_argument("--fmax-ghz", type=float); p.add_argument("--cell-mm", type=float)
    for c in registry().commands.values():  # subcommands registered by extension packages (hwflow.pipeline.hooks)
        c.add_arguments(sub.add_parser(c.name, help=c.help))
    a = ap.parse_args(argv)

    if a.cmd == "em":
        return cmd_em(a, argv)
    if a.cmd == "lib":
        return cmd_lib(a)
    if a.cmd == "block":
        return cmd_block(a)
    if a.cmd == "parts":
        return cmd_parts(a)
    if a.cmd == "run":
        from .commands.run import cmd_run
        return cmd_run(a)
    if a.cmd == "build":
        from .commands import run as runcmd
        if not a.inline and runcmd.wants_remote(a):  # the platform executor (ADR 4.9): never imported by the engine
            return runcmd.remote_build(a, runcmd.remote_options(build_options(a)))
        if a.detach:
            return runcmd.detach(a.project, list(sys.argv[1:] if argv is None else argv))

    if a.cmd == "fw":
        if a.target[0] == "test" and len(a.target) == 2:
            a.project, a.fw_test = a.target[1], True
        elif len(a.target) == 1:
            a.project, a.fw_test = a.target[0], False
        else:
            ap.error("fw: usage `fw <project> [--scaffold]` or `fw test <project>`")
    d = _load(a.project)
    if a.cmd == "netlist":
        sim = d.raw.get("sim") or {}
        an = next((x for x in sim.get("analyses", []) if x["name"] == a.analysis), {"name": "op", "cmd": "op"})
        print(spice.build_netlist(d, an)[0])
        return 0
    if a.cmd == "nets":
        return cmd_nets(d, a)
    if a.cmd == "trace":
        return cmd_trace(d)
    if a.cmd in (hooks := registry().commands):  # subcommands registered by extension packages
        return hooks[a.cmd].run(d, a)
    if a.cmd == "report":
        print(write_report(d))
        return 0
    if a.cmd == "optimize":
        return cmd_optimize(d, a)
    if a.cmd == "fw":
        return cmd_fw_test(d, a) if a.fw_test else cmd_fw(d, a)
    if a.cmd == "floorplan":
        return cmd_floorplan(d, a)

    res = run_build(d, build_options(a))
    print(res["text"])
    return res["rc"]


if __name__ == "__main__":
    sys.exit(main())
