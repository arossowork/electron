"""The Freerouting integration: the Specctra DSN that hwflow writes and the Freerouting 2.x command line."""
import copy
import subprocess

from shapely.geometry import Polygon

from hwflow.core import sexpr
from hwflow.core.libs import Libs
from hwflow.layout.board import pcb
from hwflow.layout.place import placer
from hwflow.layout.route import krt

from test_cut_and_layers import _dsn, preamp


class _ExtraPads:
    """Libs whose footprint of `ref`'s part gets extra pads (the rest is the real library)."""

    def __init__(self, libs, fp_name, extra):
        self.libs, self.fp_name, self.extra = libs, fp_name, extra

    def footprint(self, name):
        fp = self.libs.footprint(name)
        if name == self.fp_name:
            fp = {**fp, "pads": list(fp["pads"]) + self.extra}
        return fp

    def __getattr__(self, k):
        return getattr(self.libs, k)


def _export(tmp_path, d, libs):
    pcb.export_dsn(d, libs, placer.build_parts(d, libs), tmp_path / "x.dsn")
    return _dsn(tmp_path / "x.dsn")


def test_every_pin_padstack_has_a_shape_and_tabs_are_obstacles(tmp_path):
    d = preamp(tmp_path)
    base = Libs(design=d)
    ref = next(r for r, c in d.components.items() if c.get("footprint") and base.footprint(c.get("footprint"))["pads"])
    fp_name = d.components[ref].get("footprint")
    pad = base.footprint(fp_name)["pads"][0]
    mask_only = {**copy.deepcopy(pad), "number": "99", "kind": "smd", "layers": ["F.Mask"], "x": pad["x"] + 3}
    tab = {**copy.deepcopy(pad), "number": "", "x": pad["x"] - 3}  # an unnumbered copper pad (a shield tab)
    d.components[ref].data.setdefault("pins", {})["99"] = "GND"  # a net on the mask-only pad must not reach the network
    t = _export(tmp_path, d, _ExtraPads(base, fp_name, [mask_only, tab]))
    lib = sexpr.find(t, "library")
    stacks = {str(p[1]): p for p in sexpr.find_all(lib, "padstack")}
    pins = [p for im in sexpr.find_all(lib, "image") for p in sexpr.find_all(im, "pin")]
    assert pins and all(sexpr.find(stacks[str(p[1])], "shape") is not None for p in pins)  # Freerouting needs a shape
    img = next(im for im in sexpr.find_all(lib, "image") if str(im[1]).endswith(f"__{ref}"))
    ids = [str(x) for p in sexpr.find_all(img, "pin") for x in p[2:] if not isinstance(x, list)][::3]
    assert "99" not in ids and "~1" in ids  # the mask-only pad is left out, the tab is a pin of no net
    net_pins = [str(x) for n in sexpr.find_all(sexpr.find(t, "network"), "net") for x in sexpr.find(n, "pins")[1:]]
    assert f"{ref}-99" not in net_pins and not any(p.startswith(f"{ref}-~") for p in net_pins)


def test_cut_hole_keepout_carries_the_edge_clearance(tmp_path):
    d = preamp(tmp_path, regions=[{"name": "slot", "cut": True, "rect": [18, 12, 22, 16]}])
    st = sexpr.find(_export(tmp_path, d, Libs(design=d)), "structure")
    c = [float(x) / 1000 for x in sexpr.find(sexpr.find(st, "keepout"), "polygon")[3:]]
    hole = Polygon([(c[i], -c[i + 1]) for i in range(0, len(c), 2)])
    clr = min([float(d.board["clearance_mm"])] + [v["clearance"] for v in pcb.net_classes(d).values()])
    grow = max(0.0, pcb.EDGE_CLEARANCE_MM - clr)
    x0, y0, x1, y1 = hole.bounds
    assert (x0, y0, x1, y1) == tuple(round(v, 4) for v in (18 - grow, 12 - grow, 22 + grow, 16 + grow))


def test_freerouting_command_line(tmp_path, monkeypatch):
    jar = tmp_path / "fr.jar"
    jar.write_text("")
    monkeypatch.setenv("FREEROUTING", str(jar))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        (tmp_path / "t.ses").write_text("(session)")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(krt.subprocess, "run", fake_run)
    krt.run_freerouting(tmp_path / "t.dsn", tmp_path / "t.ses", passes=7)
    cmd = seen["cmd"]
    # 2.x reads settings as key=value: a separate value after the key is an unknown argument and the cap is lost
    assert "--router.autorouter.max_passes=7" in cmd and "--router.autorouter.max_passes" not in cmd
    assert f"--router.copper_to_edge_clearance_um={pcb.EDGE_CLEARANCE_MM * 1000:g}" in cmd
    assert "-da" in cmd  # no analytics upload


def test_reference_with_a_hyphen_is_quoted_in_the_network(tmp_path):
    d = preamp(tmp_path)
    old = next(r for r, c in d.components.items() if c.pins)
    d.components["L-K1"] = d.components.pop(old)  # Freerouting reads L-K1-2 as pin K1-2 of component L
    d.components["L-K1"].ref = "L-K1"
    pins = [p for r, c in d.components.items() for p in c.pins if r == "L-K1"]
    pcb.export_dsn(d, Libs(design=d), placer.build_parts(d, Libs(design=d)), tmp_path / "x.dsn")
    text = (tmp_path / "x.dsn").read_text()
    assert f'"L-K1"-{pins[0]}' in text and f" L-K1-{pins[0]}" not in text
    assert '(place "L-K1"' in text


def test_freerouting_timeout_is_an_unrouted_result_not_a_crash(tmp_path, monkeypatch):
    """Freerouting 2.x writes the session only at the end: a run stopped at the router timeout leaves none. The
    layout stage then gets "TIMEOUT after" (a budget-exhausted candidate with no copper), not a RuntimeError that
    ends the layout stage (kb_hub, kb_audio_dig of the layout corpus)."""
    jar = tmp_path / "fr.jar"
    jar.write_text("")
    monkeypatch.setenv("FREEROUTING", str(jar))

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output=b"Auto-routing pass #9")

    monkeypatch.setattr(krt.subprocess, "run", slow)
    log = krt.run_freerouting(tmp_path / "t.dsn", tmp_path / "t.ses", timeout=5)
    assert log.startswith("TIMEOUT after 5s") and not (tmp_path / "t.ses").exists()

    def crash(cmd, **kw):  # any other run without a session is still an error
        return subprocess.CompletedProcess(cmd, 1, "", "Exception in thread main")

    monkeypatch.setattr(krt.subprocess, "run", crash)
    try:
        krt.run_freerouting(tmp_path / "t.dsn", tmp_path / "t.ses")
    except RuntimeError as e:
        assert "no session file" in str(e)
    else:
        raise AssertionError("a run without a session and without a timeout must raise")
