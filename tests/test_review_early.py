"""Review early (docs/review-early.md): the checks for what design reviews kept finding late, and the floorplan's
mechanical and accessible regions."""

from pathlib import Path

import yaml

from hwflow.core import floorplan
from hwflow.core.design import Design
from hwflow.verify import reviewchecks

R = {"symbol": "Device:R", "footprint": "Resistor_SMD:R_0402_1005Metric"}


def _proj(tmp: Path, raw: dict, reqs: list | None = None, files: dict | None = None) -> Design:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    if reqs is not None:
        (tmp / "requirements.yaml").write_text(yaml.safe_dump({"product": "x", "requirements": reqs}, sort_keys=False))
    for rel, text in (files or {}).items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(text)
    return Design.load(tmp / "design.yaml")


def _mains(fused: bool, inputs=None) -> dict:
    """A terminal J1 (L in, two switched outputs) and two relay contacts, optionally a fuse first after J1."""
    lin = "AC_LIN" if fused else "AC_L"
    comps = {
        "J1": {"value": "TB3", "symbol": "Connector:Screw_Terminal_01x03", "footprint": "x:y",
               "pins": {"1": lin, "2": "AC_OUT1", "3": "AC_OUT2"}},
        "K1": {"value": "relay", "symbol": "Relay:ADW11", "footprint": "x:y", "pins": {"13": "AC_L", "14": "AC_OUT1"}},
        "K2": {"value": "relay", "symbol": "Relay:ADW11", "footprint": "x:y", "pins": {"13": "AC_L", "14": "AC_OUT2"}},
    }
    if fused:
        comps["F1"] = {"value": "T2A", "symbol": "Device:Fuse", "footprint": "x:y", "pins": {"1": "AC_LIN", "2": "AC_L"}}
    raw = {"project": "m", "board": {"width_mm": 40, "height_mm": 30,
                                     "isolation": [{"name": "mains", "nets": ["AC_*"], "creepage_mm": 5.0}]},
           "components": comps}
    if inputs:
        raw["nets"] = {n: {"role": "mains_input"} for n in inputs}
    return raw


def test_mains_fuse(tmp_path):
    f = reviewchecks.mains_fuse_findings(_proj(tmp_path / "a", _mains(False)))
    assert [x["sev"] for x in f] == ["error"] and "no overcurrent device" in f[0]["msg"]
    assert reviewchecks.mains_fuse_findings(_proj(tmp_path / "b", _mains(True))) == []
    raw = _mains(True)  # the fuse is there, but the relay K1 sits on the input before it
    raw["components"]["K1"]["pins"]["13"] = "AC_LIN"
    f = reviewchecks.mains_fuse_findings(_proj(tmp_path / "c", raw))
    assert f and f[0]["sev"] == "error" and "goes through an overcurrent device first" in f[0]["msg"]
    raw = _mains(True, inputs=["AC_LIN"])
    raw["components"]["K1"]["pins"]["13"] = "AC_LIN"
    f = reviewchecks.mains_fuse_findings(_proj(tmp_path / "d", raw))
    assert f and "mains input AC_LIN reaches K1" in f[0]["msg"]
    raw = _mains(False)
    raw["nets"] = {"AC_L": {"overcurrent": "upstream 10 A MCB, installation (R3)"}}
    assert [x["sev"] for x in reviewchecks.mains_fuse_findings(_proj(tmp_path / "e", raw))] == ["info"]
    raw = _mains(False)  # no mains marking: no check
    raw["board"]["isolation"][0]["name"] = "hv"
    assert reviewchecks.mains_fuse_findings(_proj(tmp_path / "f", raw)) == []


def test_mechanical_and_accessible_regions(tmp_path):
    raw = _mains(True)
    raw["floorplan"] = {"construction": "single", "regions": [
        {"name": "mains", "kind": "mains", "rect": [0, 0, 20, 30], "nets": ["AC_*"], "isolation_mm": 5.0},
        {"name": "cover_rivet_l", "kind": "keepout", "circle": [24, 10, 2.75]}]}
    d = _proj(tmp_path / "a", raw)
    msgs = [x["msg"] for x in reviewchecks.mechanical_findings(d)]
    assert len(msgs) == 3 and "no fixing point" in msgs[0] and "no cover" in msgs[1] and "cover_rivet_l" in msgs[2]
    assert floorplan.conductive_needs(d) == []  # not touchable as written
    raw["floorplan"]["regions"][1]["mechanical"] = "rivet"  # a rivet head is accessible by default
    raw["floorplan"]["regions"].append({"name": "cover", "kind": "other", "rect": [0, 0, 22, 30], "mechanical": "cover"})
    d = _proj(tmp_path / "b", raw)
    assert reviewchecks.mechanical_findings(d) == []
    needs = floorplan.conductive_needs(d)
    assert {(n["region"], n["keepout"], n["mm"]) for n in needs} == {("mains", "cover_rivet_l", 5.0)}
    fp = floorplan.parse(d)
    rv = next(r for r in fp["regions"] if r["name"] == "cover_rivet_l")
    assert rv["accessible"] and rv["conductive"] and rv["mechanical"] == "rivet"
    raw["floorplan"]["regions"][1]["accessible"] = False
    assert floorplan.conductive_needs(_proj(tmp_path / "c", raw)) == []
    raw["floorplan"]["regions"][1]["mechanical"] = "bolt"
    assert any("mechanical 'bolt'" in e for e in floorplan.validate(_proj(tmp_path / "d", raw)))


def test_isolation_group_reaches_accessible_keepouts(tmp_path):
    """A board.isolation group asks for its creepage from an accessible keep-out where no region asks for as much."""
    raw = _mains(True)
    raw["floorplan"] = {"construction": "single", "regions": [
        {"name": "mains", "kind": "mains", "rect": [0, 0, 20, 30], "nets": ["AC_*"], "isolation_mm": 3.0},
        {"name": "screw", "kind": "keepout", "circle": [30, 10, 2], "mechanical": "screw"}]}
    needs = floorplan.conductive_needs(_proj(tmp_path, raw))
    assert {(n["region"], n["mm"]) for n in needs} == {("mains", 3.0), ("isolation mains", 5.0)}


def test_req_file(tmp_path):
    reqs = [{"id": "R1", "priority": "must", "text": "x", "acceptance": "Analysis notes/energy_budget.md: P <= 5 uW",
             "verification": "analysis"},
            {"id": "R2", "priority": "should", "text": "see notes/ntc.md and https://x.org/a/b.pdf",
             "acceptance": "build/REPORT.md lists it", "verification": "analysis"}]
    d = _proj(tmp_path, {"project": "p", "components": {}}, reqs)
    f = reviewchecks.req_file_findings(d)
    assert [(x["sev"], x["msg"].split(":")[0]) for x in f] == [("error", "R1"), ("warn", "R2")]
    assert "notes/energy_budget.md" in f[0]["msg"] and "b.pdf" not in f[1]["msg"] and "REPORT" not in f[1]["msg"]
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "energy_budget.md").write_text("ok")
    assert [x["sev"] for x in reviewchecks.req_file_findings(d)] == ["warn"]
    assert {"notes/cost.md", "notes/energy_budget.md", "notes/ntc.md"} <= set(reviewchecks.inputs(d))


def test_cost_limits_parse():
    lims = reviewchecks.cost_limits([
        {"id": "R1", "priority": "must", "text": "Unit cost is less than 25.00 USD at 10 units"},
        {"id": "R2", "priority": "should", "acceptance": "BOM cost <= $12 at 1k"},
        {"id": "R3", "priority": "must", "text": "Price 30 EUR or less"},
        {"id": "R4", "priority": "must", "text": "costs no more battery charge than 3 mAs"},
        {"id": "R5", "priority": "must", "text": "cheap", "cost_max_usd": 7.5}])
    assert [(x["id"], x["limit"], x["strict"], x["cur"]) for x in lims] == [
        ("R1", 25.0, True, "USD"), ("R2", 12.0, False, "USD"), ("R3", 30.0, False, "EUR"), ("R5", 7.5, False, "USD")]


def _priced(extra=None):
    raw = {"project": "c", "components": {
        "U1": {"value": "MCU", "mpn": "ESP32-C3-MINI-1-N4X", "price_usd": 2.74, "symbol": "x:y", "footprint": "x:y",
               "pins": {}},
        "K1": {"value": "relay", "mpn": "ADW1103HLW", "price_usd": 4.55, "symbol": "x:y", "footprint": "x:y", "pins": {}},
        "K2": {"value": "relay", "mpn": "ADW1103HLW", "price_usd": 4.55, "symbol": "x:y", "footprint": "x:y", "pins": {}}}}
    if extra:
        raw["cost"] = {"extra": extra}
    return raw


REQ_COST = [{"id": "R42", "priority": "must", "text": "Unit cost is less than 25.00 USD at 10 units",
             "acceptance": "Analysis notes/cost.md: total < 25.00 USD"},
            {"id": "R43", "priority": "should", "text": "cost margin", "acceptance": "total <= 12.00 USD"}]


def test_cost_limit_from_design(tmp_path):
    d = _proj(tmp_path / "a", _priced(), REQ_COST)
    f = reviewchecks.cost_findings(d)  # 11.84: both met
    assert f == []
    d = _proj(tmp_path / "b", _priced([{"name": "PCB", "usd": 0.5}, {"name": "probes", "usd": 2.0},
                                       {"name": "fees", "usd": 11.0}]), REQ_COST)
    f = reviewchecks.cost_findings(d)  # 25.34
    assert [(x["sev"], x["code"], x["msg"].split(":")[0]) for x in f] == [
        ("error", "cost-limit", "R42"), ("warn", "cost-limit", "R43")]
    assert "$25.34" in f[0]["msg"] and "cost.extra $13.50" in f[0]["msg"]


COST_MD = """# Unit cost

| Item | Qty | Unit USD | Total USD |
|---|---|---|---|
| U1 ESP32-C3-MINI-1-N4 | 1 | 1.0876 | 1.09 |
| K1, K2 ADW1103HLW | 2 | 4.55 | 9.10 |
| **Electronics BOM** | | | **10.19** |
| PCB | | | 0.50 |
| **Total** | | | **14.19** |

## Re-estimate
- New total: 14.19 - 2.10 = **12.09 USD** < 25.00 (R42 met).
"""


def test_cost_doc_against_design(tmp_path):
    d = _proj(tmp_path, _priced(), REQ_COST, {"notes/cost.md": COST_MD})
    f = reviewchecks.cost_findings(d)
    by = {(x["code"], x["sev"]): x["msg"] for x in f}
    assert reviewchecks.doc_total(COST_MD) == 12.09
    assert "claims R42 met" in by[("cost-doc", "error")] and "$11.84" in by[("cost-doc", "error")]
    assert "U1 at 1.0876 (design.yaml 2.74)" in by[("cost-doc", "warn")]
    assert "K1" not in by[("cost-doc", "warn")]
    d = _proj(tmp_path, _priced([{"name": "PCB", "usd": 0.25}]), REQ_COST, {"notes/cost.md": COST_MD})
    assert [x["sev"] for x in reviewchecks.cost_findings(d) if x["msg"].startswith("notes/cost.md states")] == []
    over = COST_MD.replace("**12.09 USD** < 25.00 (R42 met)", "**30.15 USD**")
    d = _proj(tmp_path, _priced(), REQ_COST, {"notes/cost.md": over})
    f = reviewchecks.cost_findings(d)
    assert ("error", "cost-limit") in {(x["sev"], x["code"]) for x in f}


def test_model_mpn(tmp_path):
    lib = """* Hottech 1N5819WS Schottky. The leakage bound comes from the
* same-class Diodes Inc 1N5819HW (DS30217 p.2); see also RB751S40T1-D for the doc name style.
.subckt SCH_1N5819WS k a
D1 a k DS
.model DS D(IS=2u)
.ends SCH_1N5819WS
* Wrappers: they do not model OPA1622 EN.
.subckt OPA1612_DUAL a b c
.ends OPA1612_DUAL
.subckt OPA1622_DUAL a b c
.ends OPA1622_DUAL
"""
    sp = {"lib": "models/m.lib", "subckt": "SCH_1N5819WS", "ports": ["1", "2"], "source": "fit"}
    raw = {"project": "s", "components": {
        "D7": {"value": "1N5819WS", "mpn": "1N5819WS", "symbol": "Device:D", "footprint": "x:y", "pins": {}, "spice": sp},
        "D8": {"value": "1N5819WS", "mpn": "1N5819WS", "symbol": "Device:D", "footprint": "x:y", "pins": {},
               "spice": dict(sp, mpn="1N5819HW")},
        "D9": {"value": "1N5819WS", "mpn": "1N5819WS", "symbol": "Device:D", "footprint": "x:y", "pins": {},
               "spice": dict(sp, mpn="1N5819WS")},
        "U2": {"value": "TPS61299", "mpn": "TPS61299DRLR", "symbol": "x:y", "footprint": "x:y", "pins": {},
               "spice": {"lib": "models/m.lib", "subckt": "X", "mpn": "TPS61299", "source": "vendor"}},
        "U3": {"value": "OPA1612", "mpn": "OPA1612AIDR", "symbol": "x:y", "footprint": "x:y", "pins": {},
               "spice": {"lib": "models/m.lib", "subckt": "OPA1612_DUAL", "source": "vendor"}},
        "D4": {"value": "RB751", "mpn": "RB751S40T1G", "symbol": "Device:D", "footprint": "x:y", "pins": {},
               "spice": dict(sp)}}}
    f = reviewchecks.model_mpn_findings(_proj(tmp_path, raw, files={"models/m.lib": lib}))
    got = {(x["msg"].split(":")[0], x["sev"]) for x in f}
    assert got == {("D7", "warn"), ("D8", "error")}
    assert "1N5819HW" in next(x["msg"] for x in f if x["msg"].startswith("D7"))


def test_footprint_link(tmp_path):
    """Two `thermo.pretty` folders: the project's (the KiCad table gets it) without the module, a block's with it."""
    from hwflow.core.libs import Libs
    d = _proj(tmp_path / "p", {"project": "p", "components": {
        "U1": {"value": "M", "symbol": "Device:R", "footprint": "thermo:MOD", "pins": {}}}})
    (tmp_path / "p" / "lib" / "footprints" / "thermo.pretty").mkdir(parents=True)
    other = tmp_path / "blk" / "thermo.pretty"
    other.mkdir(parents=True)
    (other / "MOD.kicad_mod").write_text("(footprint \"MOD\")")
    d = Design.load(tmp_path / "p" / "design.yaml")
    libs = Libs(extra_dirs=[], design=d)
    libs.extra.append(tmp_path / "blk")
    f = reviewchecks.footprint_link_findings(d, libs)
    assert [x["sev"] for x in f] == ["error"] and "thermo:MOD" in f[0]["msg"]
    (tmp_path / "p" / "lib" / "footprints" / "thermo.pretty" / "MOD.kicad_mod").write_text("(footprint \"MOD\")")
    assert reviewchecks.footprint_link_findings(d, libs) == []


def test_erc_footprint_link_is_promoted(monkeypatch, tmp_path):
    from hwflow.pipeline import stages
    d = _proj(tmp_path, {"project": "e", "components": {"R1": {"value": "1k", **R, "pins": {"1": "A", "2": "B"}}}})
    monkeypatch.setattr(stages.kicad, "write_lib_tables", lambda *a, **k: None)
    monkeypatch.setattr(stages.pcb, "write_project", lambda *a, **k: None)
    import hwflow.schematic.sch as sch
    monkeypatch.setattr(sch, "write_schematic", lambda *a, **k: None)
    monkeypatch.setattr(stages.kicad, "erc", lambda p: [
        {"sev": "warning", "type": "footprint_link_issues", "msg": "Footprint 'X' not found in library 'thermo'"},
        {"sev": "warning", "type": "lib_symbol_mismatch", "msg": "m"}])
    res = stages.stage_sch(d, None)
    assert res["errors"] == 1 and not res["ok"] and res["violations"][0]["promoted"]


def test_check_key_facts_follow_the_prose_the_checks_read(tmp_path):
    """The check stage's key strips requirement prose; the facts the review checks read from it are hashed instead."""
    from hwflow.pipeline.spec import _check_facts
    reqs = [{"id": "R1", "priority": "must", "text": "Unit cost less than 25 USD", "acceptance": "see notes/c.md"}]
    a = _check_facts(_proj(tmp_path, {"project": "k", "components": {}}, reqs))
    reqs[0]["text"] = "The unit cost is less than 25 USD (reworded)"
    assert _check_facts(_proj(tmp_path, {"project": "k", "components": {}}, reqs)) == a
    reqs[0]["text"] = "Unit cost less than 22 USD"
    assert _check_facts(_proj(tmp_path, {"project": "k", "components": {}}, reqs)) != a


def test_cost_limit_is_a_pending_decision_not_a_stage_blocker(tmp_path, monkeypatch):
    from hwflow.pipeline import stages
    d = _proj(tmp_path, _priced([{"name": "fees", "usd": 14.0}]), REQ_COST)
    assert any(x["code"] == "cost-limit" and x["sev"] == "error" and x["decision"]
               for x in reviewchecks.cost_findings(d))
    fake = [{"sev": "error", "code": "cost-limit", "decision": True, "msg": "R42: over"},
            {"sev": "warn", "code": "x", "msg": "w"}]
    monkeypatch.setattr(stages, "check", lambda d, libs: list(fake))
    r = stages.stage_check(d, None)
    assert r["ok"] and r["errors"] == 0 and r["decisions_pending"] == 1
    fake.append({"sev": "error", "code": "abs-max", "msg": "real"})  # a real error still blocks
    r = stages.stage_check(d, None)
    assert not r["ok"] and r["errors"] == 1 and r["decisions_pending"] == 1
