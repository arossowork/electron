
from hwflow.settings import get_settings
import pytest

from hwflow import kicad, keepout

KO_ZONE = '''(zone (net 0) (net_name "") (layers "F.Cu" "B.Cu") (uuid "5b6c1a0e-3d0a-4c7e-9a11-0a1b2c3d4e0a") (name "stack") (hatch edge 0.5)
    (connect_pads (clearance 0)) (min_thickness 0.25)
    (keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) (copperpour not_allowed) (footprints not_allowed))
    (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 20 10) (xy 40 10) (xy 40 30) (xy 20 30))))'''

KO_FP = '''(footprint "kolib:KO" (layer "F.Cu") (uuid "00000000-0000-4000-8000-000000000001") (at 66 22)
  (property "Reference" "KO1" (at 0 0 0) (layer "F.Fab") (uuid "00000000-0000-4000-8000-000000000002") (effects (font (size 1 1) (thickness 0.15))))
  (attr exclude_from_pos_files exclude_from_bom)
  (pad "1" smd rect (at 0 0) (size 0.2 0.2) (layers "Dwgs.User") (uuid "00000000-0000-4000-8000-000000000003"))
  %s)''' % KO_ZONE


def part(ref, x, y, half=2.0, layer="F.Cu", yard="F.CrtYd", n=10):
    return f'''(footprint "kolib:P" (layer "{layer}") (uuid "00000000-0000-4000-8000-0000000001{n:02d}") (at {x} {y})
  (property "Reference" "{ref}" (at 0 0 0) (layer "F.Fab") (uuid "00000000-0000-4000-8000-0000000002{n:02d}") (effects (font (size 1 1) (thickness 0.15))))
  (fp_rect (start -{half} -{half}) (end {half} {half}) (stroke (width 0.05) (type solid)) (fill no) (layer "{yard}") (uuid "00000000-0000-4000-8000-0000000003{n:02d}"))
  (pad "1" smd rect (at 0 0) (size 1 1) (layers "{layer}") (uuid "00000000-0000-4000-8000-0000000004{n:02d}")))'''


def board(*fps):
    outline = "".join(f'(gr_line (start {a}) (end {b}) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))'
                      for a, b in (("0 0", "80 0"), ("80 0", "80 50"), ("80 50", "0 50"), ("0 50", "0 0")))
    return ('(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")\n (general (thickness 1.6))\n'
            ' (paper "A4")\n (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)'
            ' (31 "F.CrtYd" user) (29 "B.CrtYd" user) (35 "F.Fab" user) (39 "Dwgs.User" user))\n'
            ' (setup (pad_to_mask_clearance 0))\n (net 0 "")\n ' + outline + "\n " + "\n ".join(fps) + "\n)\n")


def test_courtyard_over_rule_area_is_reported(tmp_path):
    b = tmp_path / "b.kicad_pcb"
    b.write_text(board(KO_FP, part("INSIDE", 30, 20, n=10), part("OUTSIDE", 60, 40, n=11),
                       part("EDGE", 44, 20, half=2.0, n=12),  # courtyard x 42-46: clear of the area (x 20-40)
                       part("ROT", 30, 6, half=2.0, n=13),  # courtyard y 4-8: clear (area y 10-30)
                       part("BACK", 30, 20, layer="B.Cu", yard="B.CrtYd", n=14)))
    allv = keepout.violations(b)
    assert all(x["type"] == "items_not_allowed" and "stack" in x["msg"] and "KO1" in x["msg"] for x in allv)
    v = [x for x in allv if x["items"][0].startswith("Footprint")]
    assert sorted(x["items"][0] for x in v) == ["Footprint BACK", "Footprint INSIDE"]
    assert v[0]["at"] == [[30.0, 20.0]]
    pads = sorted(x["items"][0] for x in allv if x["items"][0].startswith("Pad"))
    assert pads == ["Pad 1 [<no net>] of BACK on B.Cu", "Pad 1 [<no net>] of INSIDE on F.Cu"]  # the pad is in the area too


def test_pad_bbox_used_without_courtyard_and_rotation(tmp_path):
    b = tmp_path / "b.kicad_pcb"
    fp = '''(footprint "kolib:Q" (layer "F.Cu") (uuid "00000000-0000-4000-8000-000000000501") (at 50 20 90)
  (property "Reference" "Q1" (at 0 0 0) (layer "F.Fab") (uuid "00000000-0000-4000-8000-000000000502") (effects (font (size 1 1) (thickness 0.15))))
  (fp_rect (start -1 -1) (end 15 1) (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd") (uuid "00000000-0000-4000-8000-000000000503")))'''
    b.write_text(board(KO_FP, fp))
    assert keepout.violations(b) == []  # rotated 90 deg: yard x 49-51, y 5-21 -> y 5-21 touches y 10-30 but x 49-51 is clear
    b.write_text(board(KO_FP, fp.replace("(at 50 20 90)", "(at 30 20 0)")))
    assert [x["items"] for x in keepout.violations(b)] == [["Footprint Q1"]]  # unrotated yard x 29-45 overlaps


def test_merge_does_not_duplicate_what_kicad_reported(tmp_path):
    b = tmp_path / "b.kicad_pcb"
    b.write_text(board(KO_FP, part("INSIDE", 30, 20)))
    extra = keepout.violations(b)
    said = [{"sev": "error", "type": "items_not_allowed", "msg": "m", "at": [None],
             "items": ["Footprint INSIDE"]},
            {"sev": "error", "type": "items_not_allowed", "msg": "m", "at": [None],
             "items": ["Pad 1 [VCC] of INSIDE on F.Cu"]}]
    assert keepout.merge(said, extra) == said
    assert len(keepout.merge([], extra)) == 2


def test_flag(monkeypatch):
    monkeypatch.delenv("HWFLOW_KEEPOUT_STRICT", raising=False)
    assert keepout.enabled() and not keepout.enabled(False)
    monkeypatch.setenv("HWFLOW_KEEPOUT_STRICT", "0")
    assert not keepout.enabled() and keepout.enabled(True)


@pytest.mark.skipif(not bool(get_settings().tools.kicad_cli), reason="kicad-cli not installed")
@pytest.mark.slow
def test_strict_drc_reports_it_with_fp_lib_table(tmp_path, monkeypatch):
    """The case of the baseline: KiCad is silent when fp-lib-table resolves the keep-out footprint."""
    monkeypatch.delenv("HWFLOW_KEEPOUT_STRICT", raising=False)
    pretty = tmp_path / "kolib.pretty"
    pretty.mkdir()
    (pretty / "KO.kicad_mod").write_text(KO_FP.replace('"kolib:KO"', '"KO"'))
    (tmp_path / "fp-lib-table").write_text(
        f'(fp_lib_table (version 7) (lib (name "kolib")(type "KiCad")(uri "{pretty}")(options "")(descr "")))\n')
    b = tmp_path / "b.kicad_pcb"
    b.write_text(board(KO_FP, part("INSIDE", 30, 20)))
    off = [v for v in kicad.drc(b, refill=False, keepout_strict=False) if v["type"] == "items_not_allowed"]
    on = [v for v in kicad.drc(b, refill=False, keepout_strict=True) if v["type"] == "items_not_allowed"]
    assert sorted(v["items"][0].split(" [")[0] for v in on) == ["Footprint INSIDE", "Pad 1"]
    assert len(off) <= len(on)  # KiCad 10.0.7 gives [] here; a fixed KiCad gives the same one
