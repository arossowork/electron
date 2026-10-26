"""Layout portfolio (off by default; HWFLOW_PORTFOLIO=1): the first attempt of `layout --auto` routes one placement
seed with several strategies at once, each a process of its own, instead of one strategy and then the next.

Measured (docs/asic-ideas.md, "Layout portfolio"): on home-thermostat-4 the portfolio took 2.3-2.6x the wall time of
the sequential attempts to a converged board and never gave a better board; the converged boards came from the
fallback to other placement seeds. So `layout --auto` keeps the sequential attempts unless HWFLOW_PORTFOLIO=1.

Strategies (STRATEGIES, in their fixed order, which is also the last tie-break):
  base               today's layout stage: placement, routing with the net-order retries, repair, prune, DRC
  preseed            the same, with the planned roads on the board before the router (roads.py, HWFLOW_ROADS=route)
  roads              road reservations in placement, then the same as preseed (HWFLOW_ROADS=1)
  order_inside_out   base, the net-order retries starting from `--ordering inside_out` (HWFLOW_KRT_ROTATE=1)
  order_mps_reverse  base, the net-order retries starting from `--mps-reverse-rounds` (HWFLOW_KRT_ROTATE=2)
Each strategy keeps its own sequential steps (net-order retries, neck-down, seed route, repair rounds, zoning
feedback). A strategy that cannot differ from base on this design is left out (strategies): preseed and roads need a
road whose copper the preseed lays (roads.SEED_WHY), roads also a movable part. The hooks in SELECT (a routability
predictor) may drop or reorder strategies before they start.

Run (run): at most `workers` strategies at once (HWFLOW_PORTFOLIO_WORKERS, default cpu count - 2, at least 1),
each in build/auto/p_<name>/ with its own process session, so a stop also ends its router processes. The parts are
placed once (build/auto/p_place/, stages.place_shared) for all the strategies that route the same placement; roads
places for itself, at the same time. The route
caches are shared: their keys hold the strategy knobs (HWFLOW_ROADS, HWFLOW_KRT_ROTATE), so two strategies never
read each other's copper. When a strategy converges (converged: ok, 0 unrouted, 0 DRC errors, no placement
violation), no other strategy starts, and the running ones get until GRACE x the winner's time; then they are
stopped. The winner is the best by score: converged first, then fewer vias, shorter track, less routing time, then
the strategy order. With no converged strategy the best partial result is kept (fewest unrouted + DRC errors,
placement violations, vias, ...), and layout --auto goes on with its sequential attempts (other placer seeds), run
with the knobs of that strategy (the fallback). The candidates depend on which strategies finished within the grace
time; HWFLOW_PORTFOLIO_GRACE=inf waits for all of them (a reproducible choice).

Per strategy the table (rows) holds status, unrouted, DRC errors, vias, track, seconds, CPU seconds and peak memory
of its process tree (RSS from /proc, sampled every POLL_S); `peak_mb` of the run is the largest sum over the
strategies running at once. The strategy knobs are layout settings (core.settings.with_layout_env), so they reach the route cache
keys. `first_attempt` copies the kept strategy's files into build/auto/a1/, where attempt 1's files always are.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.settings import get_settings, use, with_layout_env

POLL_S = 1.0
GRACE = 1.5
HEADROOM = 2  # cores left to the rest of the machine


@dataclass(frozen=True)
class Strategy:
    name: str
    env: dict = field(default_factory=dict)
    why: str = ""

    @property
    def places(self) -> bool:
        """The strategy places the parts itself (road reservations); the others route the shared placement."""
        return self.env.get("HWFLOW_ROADS") == "1"


STRATEGIES = [
    Strategy("base", {}, "today's flow"),
    Strategy("preseed", {"HWFLOW_ROADS": "route"}, "planned roads routed first"),
    Strategy("roads", {"HWFLOW_ROADS": "1"}, "road reservations in placement, roads routed first"),
    Strategy("order_inside_out", {"HWFLOW_KRT_ROTATE": "1"}, "net orders from inside_out"),
    Strategy("order_mps_reverse", {"HWFLOW_KRT_ROTATE": "2"}, "net orders from mps-reverse-rounds"),
]
SELECT: list = []  # hooks f(design, libs, [Strategy]) -> [Strategy] (e.g. a routability predictor)


def on() -> bool:
    """HWFLOW_PORTFOLIO=1 (settings layout.portfolio): off by default."""
    return get_settings().layout.portfolio


def strategies(design, libs=None) -> list[Strategy]:
    """The strategies worth running on this design, in order."""
    from ..layout.route import roads
    out = list(STRATEGIES)
    try:
        cls = roads.classify(design)
    except Exception:  # noqa: BLE001 - no classification: the road strategies are left out
        cls = {}
    if not any(why in roads.SEED_WHY for why in cls.values()):
        out = [s for s in out if s.name not in ("preseed", "roads")]
    def fixed(c):
        pl = c.get("placement") or {}
        return "x" in pl and "y" in pl and bool(pl.get("fixed", True))
    movable = any(not fixed(c) for c in design.components.values() if not c.get("virtual"))
    if not movable:
        out = [s for s in out if s.name != "roads"]
    for f in SELECT:
        out = list(f(design, libs, out))
    return out


def converged(r: dict | None) -> bool:
    return bool(r and r.get("ok") and not r.get("unrouted") and not r.get("drc_errors")
                and not r.get("placement_violations"))


def _track(r) -> float:
    return round(sum(v.get("length_mm", 0) for v in (r.get("nets") or {}).values()), 1)


def score(r: dict | None, index: int) -> tuple:
    """Lower is better: converged strategies first (vias, track, routing time), then partial results."""
    if r is None:
        return (2, math.inf, index)
    route_s = (r.get("timing") or {}).get("route_s") or r.get("seconds") or 0.0
    if converged(r):
        return (0, r["vias"], _track(r), round(route_s, 1), index)
    return (1, r.get("unrouted", 0) + r.get("drc_errors", 0), len(r.get("placement_violations") or []),
            r.get("vias", 0), _track(r), index)


# ------------------------------------------------------------------ process tree accounting (/proc)

def _sessions() -> dict[int, tuple[int, float]]:
    """session id -> (RSS bytes, CPU seconds incl. waited children) over its processes."""
    page = os.sysconf("SC_PAGE_SIZE")
    tick = os.sysconf("SC_CLK_TCK")
    out: dict = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/stat") as f:
                s = f.read()
        except OSError:
            continue
        rest = s[s.rfind(")") + 2:].split()
        sid = int(rest[3])
        cpu = sum(int(rest[i]) for i in (11, 12, 13, 14)) / tick
        rss = int(rest[21]) * page
        a, c = out.get(sid, (0, 0.0))
        out[sid] = (a + rss, c + cpu)
    return out


def _stop(pid: int):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        t = time.time()
        while time.time() - t < 5:
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    return
            except ChildProcessError:
                return
            time.sleep(0.1)


# ------------------------------------------------------------------ one strategy (child process)

def _placing(place, k: int, path: Path):
    os.setsid()
    path.parent.mkdir(parents=True, exist_ok=True)
    log = open(path.parent / "portfolio.log", "w", buffering=1)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    try:
        place(k, path)
    except Exception as e:  # noqa: BLE001 - the strategies then place for themselves
        print(f"shared placement failed: {type(e).__name__}: {e}")
    os._exit(0)


def _lane(strategy: Strategy, workdir: Path, k: int, attempt=None, placement: Path | None = None):
    os.setsid()
    workdir.mkdir(parents=True, exist_ok=True)
    log = open(workdir.parent / f"{workdir.name}.log", "w", buffering=1)  # beside: the attempt clears workdir
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    if attempt is None:
        from . import stages
        attempt = stages._auto_attempt
    try:
        with use(with_layout_env(get_settings(), strategy.env)):  # the strategy's knobs, in this process only
            res = attempt(k, workdir=workdir, env=strategy.env, **({"placement": placement} if placement else {}))
    except Exception as e:  # noqa: BLE001
        res = {"k": k + 1, "error": f"{strategy.name}: {type(e).__name__}: {str(e)[:160]}"}
    (workdir / "portfolio.json").write_text(json.dumps(res, default=str))
    os._exit(0)


def run(out: Path, k: int = 0, workers: int | None = None, deadline: float | None = None,
        design=None, libs=None, lanes: list[Strategy] | None = None, log=print, attempt=None, place=None) -> dict:
    """The portfolio of attempt k (_run). On a design with an area search (board.optimize_area) the search places
    and routes at every size, so it cannot be shared: base runs first, alone, and the other strategies (each with its
    own search) run at once only when base does not converge."""
    lanes = list(lanes if lanes is not None else strategies(design, libs))
    kw = dict(workers=workers, deadline=deadline, design=design, libs=libs, log=log, attempt=attempt, place=place)
    staged = design is not None and design.board.get("optimize_area") and len(lanes) > 1 and lanes[0].name == "base"
    if not staged:
        return _run(out, k, lanes=lanes, **kw)
    log("[portfolio] area search: base first, the other strategies only if it does not converge")
    first = _run(out, k, lanes=lanes[:1], **kw)
    if converged(first.get("r")):
        first["rows"] += [{"strategy": s.name, "status": "not started (base converged)"} for s in lanes[1:]]
        first["line"] = first["line"].replace("of 1]", f"of {len(lanes)}]")
        return first
    rest = _run(out, k, lanes=lanes[1:], **kw)
    both = [(score(x.get("r"), i), x) for i, x in ((0, first), (1 + _index(lanes[1:], rest), rest)) if x.get("r")]
    best = min(both, key=lambda t: t[0])[1] if both else rest
    rows = first["rows"] + rest["rows"]
    for row in rows:
        row["kept"] = bool(best.get("strategy")) and row["strategy"] == best["strategy"]
    return dict(best, rows=rows, peak_mb=max(first["peak_mb"], rest["peak_mb"]),
                cpu_s=round(first["cpu_s"] + rest["cpu_s"], 1), seconds=round(first["seconds"] + rest["seconds"], 1),
                **({} if both else {"error": f"{first.get('error')}; {rest.get('error')}"}))


def _index(lanes, res) -> int:
    return next((i for i, s in enumerate(lanes) if s.name == res.get("strategy")), len(lanes))


def _run(out: Path, k: int = 0, workers: int | None = None, deadline: float | None = None,
         design=None, libs=None, lanes: list[Strategy] | None = None, log=print, attempt=None, place=None) -> dict:
    """Run the strategies of attempt k and pick the best. `attempt(k, workdir, env, placement)`: the attempt function
    (default stages._auto_attempt, with stages._AUTO set). `place(k, path) -> bool`: the shared placement
    (stages.place_shared); when given and two or more strategies route the same placement, it runs once in a process of
    its own (one worker slot) and those strategies start when it is written (not written: they place for
    themselves). Returns {k, r, line, workdir, strategy,
    rows, peak_mb, cpu_s, seconds} like a stages.run_attempt result, or {k, error, rows} when every one failed."""
    import multiprocessing as mp
    lanes = list(lanes if lanes is not None else strategies(design, libs))
    cfg = get_settings().layout
    n = max(1, cfg.portfolio_workers or (os.cpu_count() or 2) - HEADROOM)
    workers = max(1, min(len(lanes), workers or n))
    grace = cfg.portfolio_grace
    ctx = mp.get_context("fork")
    t0 = time.time()
    queue = list(enumerate(lanes))
    shared = Path(out) / "auto" / "p_place" / "placement.json"
    placing = None  # the shared placement process while it runs; then `share` says whether it was written
    share = place is not None and sum(not s.places for s in lanes) > 1
    if share:
        shutil.rmtree(shared.parent, ignore_errors=True)
        placing = ctx.Process(target=_placing, args=(place, k, shared), daemon=False)
        placing.start()
        log("[portfolio] shared placement started")
    place_t = None
    running: dict = {}  # index -> (process, start, workdir)
    rows: dict = {}
    res_of: dict = {}
    peak, cpu_of, rss_of = 0, {}, {}
    win_t, kill_at = None, None
    while queue or running or placing:
        while queue and len(running) + (placing is not None) < workers and win_t is None \
                and (deadline is None or time.time() < deadline):
            ready = [x for x in queue if x[1].places or not share or placing is None]
            if not ready:
                break
            queue.remove(ready[0])
            i, s = ready[0]
            w = Path(out) / "auto" / f"p_{s.name}"
            if w.exists():
                shutil.rmtree(w, ignore_errors=True)
            pl = shared if share and not s.places else None
            p = ctx.Process(target=_lane, args=(s, w, k, attempt, pl), daemon=False)
            p.start()
            running[i] = (p, time.time(), w)
            log(f"[portfolio] {s.name} started ({s.why})")
        time.sleep(POLL_S)
        ses = _sessions()
        tot = 0
        if placing is not None:
            rss, cpu = ses.get(placing.pid, (0, 0.0))
            tot += rss
            cpu_of["place"] = max(cpu_of.get("place", 0.0), cpu)
            if not placing.is_alive():
                placing.join()
                placing = None
                place_t = round(time.time() - t0, 1)
                share = shared.exists()
                log(f"[portfolio] shared placement {'done' if share else 'not shared'} in {place_t:.0f} s")
        for i, (p, st, w) in running.items():
            rss, cpu = ses.get(p.pid, (0, 0.0))
            tot += rss
            rss_of[i] = max(rss_of.get(i, 0), rss)
            cpu_of[i] = max(cpu_of.get(i, 0.0), cpu)
        peak = max(peak, tot)
        now = time.time()
        for i in list(running):
            p, st, w = running[i]
            if p.is_alive():
                continue
            p.join()
            del running[i]
            f = w / "portfolio.json"
            res = json.loads(f.read_text()) if f.exists() else {"error": f"{lanes[i].name}: no result (exit {p.exitcode})"}
            res_of[i] = res
            r = res.get("r")
            rows[i] = _row(lanes[i], res, now - st, cpu_of.get(i), rss_of.get(i))
            log(f"[portfolio] {lanes[i].name}: {rows[i]['status']} in {rows[i]['seconds']:.0f} s"
                + (f", {r['unrouted']} unrouted, DRC {r['drc_errors']} errors, {r['vias']} vias" if r else
                   f" ({res.get('error')})"))
            if converged(r) and win_t is None:
                win_t = now - t0
                kill_at = t0 + grace * win_t
                for j, s in queue:
                    rows[j] = {"strategy": s.name, "status": "not started (a strategy converged)"}
                queue = []
        over = deadline is not None and now >= deadline
        if over and placing is not None:
            _stop(placing.pid)
            placing.join(1)
            placing, share = None, False
        if running and ((kill_at is not None and now >= kill_at) or over):
            for i in list(running):
                p, st, w = running.pop(i)
                _stop(p.pid)
                p.join(1)
                rows[i] = dict(_row(lanes[i], {}, now - st, cpu_of.get(i), rss_of.get(i)),
                               status="stopped (budget)" if over else f"stopped ({grace:g} x the winner's time)")
                log(f"[portfolio] {lanes[i].name}: {rows[i]['status']}")
        if over:
            for j, s in queue:
                rows[j] = {"strategy": s.name, "status": "not started (budget)"}
            queue = []
    cands = [(score(res_of[i].get("r"), i), i) for i in res_of if res_of[i].get("r")]
    table = [rows[i] for i in sorted(rows)]
    base = {"rows": table, "peak_mb": round(peak / 2 ** 20), "cpu_s": round(sum(cpu_of.values()), 1),
            "seconds": round(time.time() - t0, 1), "workers": workers, "place_s": place_t,
            "place_cpu_s": round(cpu_of.get("place", 0.0), 1) if place_t is not None else None}
    if not cands:
        return dict(base, k=k + 1, error="portfolio: " + "; ".join(str(res_of[i].get("error")) for i in res_of))
    _, i = min(cands)
    res = res_of[i]
    for row in table:
        row["kept"] = row["strategy"] == lanes[i].name
    line = f"{res['line']} [strategy {lanes[i].name} of {len(lanes)}]"
    return dict(base, k=k + 1, r=res["r"], line=line, workdir=str(Path(out) / "auto" / f"p_{lanes[i].name}"),
                strategy=lanes[i].name, env=dict(lanes[i].env))


def _row(s: Strategy, res: dict, secs: float, cpu, rss) -> dict:
    r = res.get("r")
    row = {"strategy": s.name, "seconds": round(secs, 1), "cpu_s": round(cpu or 0.0, 1),
           "peak_mb": round((rss or 0) / 2 ** 20)}
    if r:
        row.update(status="converged" if converged(r) else "partial", unrouted=r.get("unrouted"),
                   drc_errors=r.get("drc_errors"), violations=len(r.get("placement_violations") or []),
                   vias=r.get("vias"), track_mm=_track(r), route_s=(r.get("timing") or {}).get("route_s"))
    else:
        row.update(status="error", reason=str(res.get("error") or "no result")[:160])
    return row


def report_lines(p: dict) -> list[str]:
    """REPORT.md lines for a portfolio result (status.json layout.portfolio)."""
    L = [f"- layout portfolio: {len(p['rows'])} strategies, {p['workers']} at once, {p['seconds']:.0f} s, "
         f"CPU {p['cpu_s']:.0f} s, peak memory {p['peak_mb']} MB; kept {p.get('strategy') or 'none'}"
         + (f"; shared placement {p['place_s']:.0f} s (CPU {p.get('place_cpu_s') or 0:.0f} s)" if p.get("place_s") else "")
         + (f"; none converged, fallback attempt {p['fallback']['attempt']} kept (knobs of "
            f"{p['fallback']['knobs_of']})" if p.get("fallback") else ""),
         "", "| strategy | status | unrouted | DRC errors | vias | track mm | s | CPU s | peak MB |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in p["rows"]:
        L.append(f"| {r['strategy']}{' (kept)' if r.get('kept') else ''} | {r.get('status')} | {r.get('unrouted', '-')} | "
                 f"{r.get('drc_errors', '-')} | {r.get('vias', '-')} | {r.get('track_mm', '-')} | {r.get('seconds', '-')} | "
                 f"{r.get('cpu_s', '-')} | {r.get('peak_mb', '-')} |")
    return L + [""]


SUMMARY = ("rows", "peak_mb", "cpu_s", "seconds", "workers", "strategy", "place_s", "place_cpu_s")


def first_attempt(out: Path, design, libs, deadline: float | None = None, attempt=None, place=None,
                  log=print) -> dict:
    """Attempt 1 of the layout search as a portfolio (run): the kept strategy's files are copied to build/auto/a1/
    (where the selection and the stage outputs read attempt 1), each strategy's metrics.json is kept as
    build/auto/p_<name>.metrics.json (beside its .log), and its work folder is removed. Returns {k, r, line} or
    {k, error} as stages.run_attempt, plus `env` (the kept strategy's knobs, for the fallback attempts); r["portfolio"]
    is the table for status.json and REPORT.md (report_lines)."""
    pf = run(out, 0, deadline=deadline, design=design, libs=libs, attempt=attempt, place=place, log=log)
    adir = Path(out) / "auto"
    if pf.get("workdir"):
        a1 = adir / "a1"
        shutil.rmtree(a1, ignore_errors=True)
        shutil.copytree(pf["workdir"], a1)
    for x in list(adir.glob("p_*")):
        if x.is_dir():
            if (x / "metrics.json").exists():
                shutil.copy(x / "metrics.json", adir / f"{x.name}.metrics.json")
            shutil.rmtree(x, ignore_errors=True)
    res = {key: pf[key] for key in ("k", "r", "line", "error", "env") if key in pf}
    if res.get("r") is not None:
        res["r"] = dict(res["r"], portfolio={key: pf.get(key) for key in SUMMARY})
    return res

