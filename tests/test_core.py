import math

import pytest

from hwflow import sexpr
from hwflow.design import Design, check, parse_value, snap_e
from hwflow.pcb import ipc2221_width_mm
from hwflow.postlayout import microstrip

EX = "examples/sensor_preamp"


@pytest.mark.parametrize("s,v", [("4k7", 4700), ("100n", 1e-7), ("2.2meg", 2.2e6), ("1M", 1e6), ("10", 10),
                                 ("4R7", 4.7), ("90.9k", 90900), ("1uF", 1e-6), (3.3, 3.3)])
def test_parse_value(s, v):
    assert math.isclose(parse_value(s), v, rel_tol=1e-9)


def test_snap_e():
    assert math.isclose(snap_e(4800, "E24"), 4700)
    assert math.isclose(snap_e(9.4e-9, "E12"), 10e-9)


def test_sexpr_roundtrip():
    t = sexpr.loads('(a (b "x y" 1.5) (c d))')
    assert sexpr.loads(sexpr.dumps(t)) == t
    assert sexpr.find(t, "b")[1] == "x y"


def test_ipc2221_one_amp():
    # 1 A, 1 oz, 10 C rise, external layer: about 0.3 mm (12 mil)
    assert 0.25 < ipc2221_width_mm(1.0) < 0.35


def test_microstrip_50_ohm():
    z0, ee = microstrip(2.9, 1.6, 0.035, 4.3)
    assert 47 < z0 < 53 and 3.0 < ee < 3.6


def test_design_check_clean():
    from hwflow.libs import Libs
    d = Design.load(EX)
    assert [f for f in check(d, Libs()) if f["sev"] == "error"] == []


def test_spice_ldo_and_gain(tmp_path):
    from hwflow import spice
    d = Design.load(EX)
    res = {r["name"]: r for a in spice.run_all(d, tmp_path) for r in a["rows"]}
    assert abs(res["v3v3"]["value"] - 3.30) < 0.01
    assert abs(res["gain_db"]["value"] - 20.0) < 0.2
