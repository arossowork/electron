"""Reference reuse (docs/reference-reuse.md): `hwflow ref search`, the `--json` views, the parts-time pin checks and
the block placement seed."""

import json
from pathlib import Path

import pytest
import yaml

from hwflow import cli
from hwflow.core import pinchecks, refs
from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.pipeline import summary

R = {"symbol": "Device:R", "footprint": "Resistor_SMD:R_0402_1005Metric"}
C = {"symbol": "Device:C", "footprint": "Capacitor_SMD:C_0402_1005Metric"}


def _write(path: Path, raw: dict) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def _boost_design(name="boostboard"):
    return {
        "project": name, "description": "2 AAA to 3.3 V boost", "license": "CERN-OHL-S-2.0",
        "board": {"width_mm": 30, "height_mm": 20, "layers": 2},
        "nets": {"GND": {"class": "ground"}, "+3V3": {"class": "power"}},
        "components": {
            "U1": {"value": "TPS61299", "mpn": "TPS61299DRLR", "symbol": "thermo:TPS61299", "group": "boost",
                   "footprint": "Package_TO_SOT_SMD:SOT-563",
                   "pins": {"1": "VIN", "2": "SW", "3": "VIN", "4": "GND", "5": "+3V3", "6": "GND"}},
            "L1": {"value": "1u", "symbol": "Device:L", "footprint": "Inductor_SMD:L_0805_2012Metric",
                   "pins": {"1": "VIN", "2": "SW"}},
            "C1": {"value": "10u", **C, "pins": {"1": "+3V3", "2": "GND"}, "decouples": "U1"},
            "C2": {"value": "2.2u", **C, "pins": {"1": "VIN", "2": "GND"}, "decouples": "U1"},
            "C3": {"value": "100n", **C, "pins": {"1": "+3V3", "2": "GND"}, "decouples": "U9"},
            "TP1": {"value": "TP", "symbol": "Connector:TestPoint", "footprint": "TestPoint:TestPoint_Pad_D1.0mm",
                    "pins": {"1": "SW"}},
        }}


# ------------------------------------------------------------------ (a) reference search

def test_ref_search_finds_the_subcircuit_around_an_mpn_prefix(tmp_path):
    _write(tmp_path / "refs" / "boostboard", _boost_design())
    ds = refs.load_local([tmp_path / "refs"])
    res = refs.search(["TPS61299"], designs=ds)
    assert res["schema"] == "hwflow.ref/1"
    h = res["hits"][0]
    assert h["anchor"]["ref"] == "U1" and h["license"] == "CERN-OHL-S-2.0"
    got = {e["ref"] for e in h["neighbours"]}
    assert {"L1", "C1", "C2"} <= got
    assert "C3" not in got  # it decouples another IC
    assert "TP1" not in got  # test points are not circuit
    assert "SW" in {n for e in h["neighbours"] for n in e["nets"]}


def test_ref_search_by_function_word_and_dedup(tmp_path):
    _write(tmp_path / "refs" / "a", _boost_design("a"))
    _write(tmp_path / "refs" / "b", _boost_design("b"))
    res = refs.search(["boost"], designs=refs.load_local([tmp_path / "refs"]))
    assert len(res["hits"]) == 1 and res["hits"][0]["also_in"] == ["b"]  # same sub-circuit once
    assert refs.search(["nonexistent123"], designs=refs.load_local([tmp_path / "refs"]))["hits"] == []


def test_oshw_index_schema_with_and_without_netlist(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"designs": [
        {"name": "open-thermo", "url": "https://example.org/open-thermo", "license": "MIT", "mcu": "ESP32-C3",
         "blocks": ["boost", "ntc"], "mpns": ["TPS61299DRLR", "ADS1115IDGSR"], "layers": 2, "size": [50, 40]},
        {"name": "with-netlist", "url": "https://example.org/n", "license": "CERN-OHL-P-2.0",
         "parts": {"U3": {"value": "TPS61299", "mpn": "TPS61299DRLR", "pins": {"1": "VIN", "2": "SW"}},
                   "L3": {"value": "1u", "pins": {"1": "VIN", "2": "SW"}}}},
        {"no": "name"}, "junk"]}))
    ext, why = refs.load_oshw(idx)
    assert why is None and [d.name for d in ext] == ["open-thermo", "with-netlist"]
    res = refs.search(["TPS61299"], designs=ext)
    kinds = {h["design"]: h for h in res["hits"]}
    assert kinds["with-netlist"]["neighbours"][0]["ref"] == "L3"
    assert kinds["open-thermo"]["license"] == "MIT" and kinds["open-thermo"]["size"] == "50x40 mm"
    assert "anchor" not in kinds["open-thermo"]
    missing, why = refs.load_oshw(tmp_path / "nope.json")
    assert missing == [] and "no external index" in why
    (tmp_path / "bad.json").write_text("{not json")
    assert refs.load_oshw(tmp_path / "bad.json")[1].startswith("external index")


def test_ref_cli_json_and_text(tmp_path, monkeypatch, capsys):
    _write(tmp_path / "refs" / "boostboard", _boost_design())
    monkeypatch.setenv("HWFLOW_REF_DIRS", str(tmp_path / "refs"))
    monkeypatch.setenv("HWFLOW_OSHW_INDEX", str(tmp_path / "missing.json"))
    assert cli.main(["ref", "search", "TPS61299", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sources"]["local"] == 1 and any(h["design"] == "boostboard" for h in out["hits"])
    assert cli.main(["ref", "search", "TPS61299"]) == 0
    text = capsys.readouterr().out
    assert "boostboard" in text and "L1" in text
    assert cli.main(["ref", "search", "ZZZ9999"]) == 1


def test_block_library_is_searched():
    res = refs.search(["MAX17227"], designs=refs.load_blocks())
    assert res["hits"] and res["hits"][0]["block"] == "max17227a_boost"


# ------------------------------------------------------------------ (b) JSON views

def test_json_views_are_compact_and_stable(tmp_path):
    d = Design.load(_write(tmp_path / "p", _boost_design()))
    out = d.dir / "build"
    out.mkdir()
    from hwflow.pipeline.status import _design_hash
    h = _design_hash(d)
    st = {"check": {"ok": False, "errors": 1, "warnings": 1, "design_hash": h, "at": 1,
                    "findings": [{"sev": "error", "code": "x", "msg": "bad"}, {"sev": "warn", "code": "y", "msg": "w"},
                                 {"sev": "info", "code": "z", "msg": "i"}]},
          "layout": {"ok": False, "design_hash": h, "at": 2, "board_mm": [30, 20], "unrouted": 1, "drc_errors": 3,
                     "drc_warnings": 0, "vias": 4,
                     "drc": [{"sev": "error", "type": "clearance", "msg": "m", "items": ["a", "b", "c"], "at": [[1, 2]]}] * 3
                     + [{"sev": "warning", "type": "silk", "msg": "s", "items": []}],
                     "nets": {"SW": {"width": 0.6, "min_w": 0.2, "max_w": 0.6, "length_mm": 3.14159, "narrow_mm": 0.5,
                                     "vias": 0, "layers": ["F.Cu"]}}},
          "score": {"drc_errors": 3}}
    (out / "status.json").write_text(json.dumps(st))
    (out / "REPORT.md").write_text("# x\nUpdated 2026-10-27.\nVerdict: FAIL: layout/DRC errors\n")
    c = summary.check_json(d)
    assert c["schema"] == "hwflow.check/1" and [x["code"] for x in c["findings"]] == ["x", "y"]
    v = summary.build_view(d)
    assert v["verdict"].startswith("FAIL") and v["layout"]["drc_by_type"] == {"clearance": 3}
    assert v["layout"]["drc_top"][0]["items"] == ["a", "b"] and v["score"] == {"drc_errors": 3}
    n = summary.nets_json(st["layout"]["nets"], ["S*"])
    assert n["cols"][0] == "class_w" and n["nets"]["SW"][3] == 3.14
    t = summary.trace_json({"rows": [{"id": "R1", "status": "pass"}, {"id": "R2", "status": "UNVERIFIED",
                                                                       "priority": "must", "evidence_reason": "none"}],
                            "total": 2, "covered": 1, "failing": 0, "unverified": 1})
    assert t["verified_ids"] == ["R1"] and t["open"][0]["id"] == "R2" and "text" not in t["open"][0]


# ------------------------------------------------------------------ (c) pin checks

def _relay_design(v3v3_max=None, coil_limit=3.3):
    nets = {"GND": {"class": "ground"}, "+3V3": {"class": "power"}}
    if v3v3_max is not None:
        nets["+3V3"]["v_max"] = v3v3_max
    return {
        "project": "relay", "board": {"width_mm": 30, "height_mm": 20}, "nets": nets,
        "components": {
            "U4": {"value": "DRV8837", "symbol": "Driver_Motor:DRV8837",
                   "footprint": "Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm",
                   "pins": {"1": "+3V3", "2": "K_A", "3": "K_B", "4": "GND", "5": "IN2", "6": "IN1", "7": "nSLEEP",
                            "8": "+3V3", "9": "GND"},
                   "limits": {"abs_max_v": 12, "source": "DRV8837 ds 6.1"}},
            "K1": {"value": "relay", "symbol": "Relay:Relay_SPST_Latching_1coil",
                   "footprint": "Relay_THT:Relay_SPST_Omron-G5Q-1", "pins": {"A1": "K_A", "A2": "K_B", "13": "AC1", "14": "AC2"},
                   "limits": {"pins": {"A1": coil_limit, "A2": coil_limit}, "source": "coil max applied voltage, ds p.2"}},
            "J1": {"value": "J", "symbol": "Connector_Generic:Conn_01x02",
                   "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                   "pins": {"1": "AC1", "2": "AC2"}, "limits": {"hole_mm": 1.1, "source": "plug ds"}},
            "U5": {"value": "MCU", "symbol": "Comparator:LM393", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                   "pins": {"1": "IN1", "2": "GND", "3": "GND", "5": "GND", "6": "GND", "7": "IN2", "4": "GND", "8": "+3V3"}},
            "R1": {"value": "10k", **R, "pins": {"1": "+3V3", "2": "nSLEEP"}},
            "F1": {"value": "T2A", "symbol": "Device:Fuse", "footprint": "Fuse:Fuse_1206_3216Metric",
                   "pins": {"1": "AC1", "2": "AC3"}, "current_rating_a": 2.0},
            "C9": {"value": "1u", **C, "pins": {"1": "+3V3", "2": "GND"}, "voltage_rating_v": 2.5},
        }}


@pytest.fixture(scope="module")
def libs():
    return Libs()


def _codes(raw, tmp_path, libs, name="d"):
    d = Design.load(_write(tmp_path / name, raw))
    return pinchecks.findings(d, libs)


def test_abs_max_catches_the_rail_above_the_relay_coil_limit(tmp_path, libs):
    # thermo-e2e-1 review 1 #11: +3V3 may reach 3.42 V against a 3.3 V coil maximum; a nominal rail passes
    nominal = {(x["code"]) for x in _codes(_relay_design(), tmp_path, libs, "a")}
    assert "abs-max" not in nominal
    f = _codes(_relay_design(v3v3_max=3.42), tmp_path, libs, "b")
    msgs = [x["msg"] for x in f if x["code"] == "abs-max"]
    assert len(msgs) == 2 and all("K1" in m and "bound from its drivers" in m for m in msgs)


def test_pin_checks_open_drain_fuse_hole_cap(tmp_path, libs):
    raw = _relay_design()
    raw["nets"]["AC1"] = {"current_a": 1.9}
    raw["components"]["D5"] = {"value": "SMAJ5.0A", "symbol": "Device:D_TVS", "footprint": "Diode_SMD:D_SMA",
                               "pins": {"1": "+3V3", "2": "GND"}}
    f = _codes(raw, tmp_path, libs)
    assert any(x["code"] == "tvs-budget" and "D5" in x["msg"] for x in f)
    raw["budgets"] = [{"name": "tvs", "kind": "copper_len", "path": ["D5.1 -> U4.1"], "max_mm": 5, "source": "t"}]
    assert "tvs-budget" not in {x["code"] for x in _codes(raw, tmp_path, libs, "g")}
    codes = {x["code"]: x["msg"] for x in f}
    assert "IN1" in codes["open-drain-pullup"] or "IN2" in codes["open-drain-pullup"]
    assert "95 %" in codes["fuse-derating"]
    assert "1.1 mm" in codes["hole-size"]  # PinHeader drills 1.0 mm
    assert "C9" in codes["cap-voltage"]
    raw["nets"]["IN1"] = {"pullup": "internal"}
    raw["nets"]["IN2"] = {"pullup": "internal"}
    assert "open-drain-pullup" not in {x["code"] for x in _codes(raw, tmp_path, libs, "e")}


def test_reset_state_and_pin_role(tmp_path, libs):
    # home-thermostat-3 D20 / review: a control input (DRV8837 nSLEEP) driven only by an MCU GPIO floats in reset
    raw = _relay_design()
    del raw["components"]["R1"]
    raw["components"]["U6"] = {"value": "ATtiny85", "symbol": "MCU_Microchip_ATtiny:ATtiny85-20S",
                               "footprint": "Package_SO:SOIC-8W_5.3x5.3mm_P1.27mm",
                               "pins": {"1": "X1", "2": "X2", "3": "X3", "4": "VOUTX", "5": "nSLEEP", "6": "IN1",
                                        "7": "IN2", "8": "+3V3"}}
    raw["components"]["R7"] = {"value": "10k", **R, "pins": {"1": "VOUTX", "2": "X1"}}
    raw["components"]["R8"] = {"value": "10k", **R, "pins": {"1": "X2", "2": "X3"}}
    f = _codes(raw, tmp_path, libs)
    codes = {x["code"]: x["msg"] for x in f}
    assert "nSLEEP" in codes["reset-state"]
    assert "U6 pin 4 (GND) is on VOUTX" in codes["pin-role"]
    raw["components"]["R1"] = {"value": "10k", **R, "pins": {"1": "GND", "2": "nSLEEP"}}  # a pull-down: defined
    assert "reset-state" not in {x["code"] for x in _codes(raw, tmp_path, libs, "f")}


def test_name_voltage():
    assert pinchecks.name_voltage("+3V3") == 3.3 and pinchecks.name_voltage("5V_SYS") == 5.0
    assert pinchecks.name_voltage("P10V5_RAW") == 10.5 and pinchecks.name_voltage("N10V") == -10.0
    assert pinchecks.name_voltage("VBAT") is None and pinchecks.name_voltage("SDA") is None


# ------------------------------------------------------------------ (d) block presets

def test_block_match_reproduces_the_hand_bindings_and_passes_check(tmp_path):
    from hwflow.core import blocks
    src = Path(__file__).resolve().parents[1] / "projects" / "home-thermostat-4" / "design.yaml"
    raw = yaml.safe_load(src.read_text())
    raw.pop("blocks", None)
    d = Design.load(_write(tmp_path / "t4", raw))
    got = {m["block"]: m for m in blocks.suggest(d)}
    # home-thermostat-4-blocks binds these four by hand (round-8 blocks)
    assert {"ads1115_3ch_rc", "pfet_switch_rc", "xtal32k_3215", "pfet_vbat_divider"} <= set(got)
    assert got["xtal32k_3215"]["parts"] == {"Y": "Y1", "CP": "C9", "CN": "C10"}
    raw["blocks"] = {m["entry"]: {"use": m["block"], "parts": m["parts"]} for m in blocks.suggest(d)}
    for m in blocks.suggest(d):
        for ref in m["parts"].values():
            raw["components"][ref].pop("placement", None)
    d2 = Design.load(_write(tmp_path / "t4b", raw))
    errs = [x for x in blocks.findings(d2) if x["sev"] == "error"]
    assert errs == []
    assert blocks.suggest(d2) == []  # nothing left to bind


def test_check_and_build_json_print_one_object(tmp_path, capsys):
    import shutil
    src = Path(__file__).resolve().parents[1] / "examples" / "sensor_preamp"
    shutil.copytree(src, tmp_path / "sp", ignore=shutil.ignore_patterns("build"))
    cli.main(["check", str(tmp_path / "sp"), "--json"])
    out = capsys.readouterr()
    c = json.loads(out.out)
    assert c["schema"] == "hwflow.check/1" and c["errors"] == 0 and "[check]" in out.err
    cli.main(["build", str(tmp_path / "sp"), "--until", "check", "--json"])
    v = json.loads(capsys.readouterr().out)
    assert v["schema"] == "hwflow.summary/1" and v["check"]["ok"] and v["run_id"]
    assert cli.main(["summary", str(tmp_path / "sp"), "--json"]) in (0, 1)
    assert json.loads(capsys.readouterr().out)["project"] == "sensor_preamp"
