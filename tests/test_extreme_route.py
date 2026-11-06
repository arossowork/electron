"""The extreme routing round (layout/route/extreme.py): which nets it may route narrower, and that it restores the
widths it does not keep."""
import yaml

from hwflow.core.design import Design
from hwflow.layout.route import extreme


def _design(tmp_path):
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({
        "project": "t", "board": {"width_mm": 20, "height_mm": 20, "layers": 2, "track_mm": 0.254},
        "nets": {"SIG": {"class": "signal"}, "RF": {"impedance_ohm": 50, "width_mm": 1.1},
                 "VIN": {"current_a": 2.0}, "V3": {"class": "power"}, "FB": {"min_width_mm": 0.3},
                 "THIN": {"width_mm": 0.15}, "GND": {"class": "ground"}},
        "components": {"R1": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                              "pins": {"1": "SIG", "2": "RF"}},
                       "R2": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                              "pins": {"1": "VIN", "2": "V3"}},
                       "R3": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                              "pins": {"1": "FB", "2": "THIN"}},
                       "R4": {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                              "pins": {"1": "GND", "2": "SIG"}}}}))
    return Design.load(tmp_path)


def test_only_nets_without_an_electrical_width_rule_are_narrowed(tmp_path):
    d = _design(tmp_path)
    # impedance, current, power class, min_width_mm and the ground pour keep their width; THIN is at the floor already
    assert extreme.narrowable(d) == {"SIG": 0.254}


def test_widths_are_set_and_restored(tmp_path):
    d = _design(tmp_path)
    extreme._set_widths(d, {"SIG": extreme.EXTREME_TRACK_MM, "RF": 0.8})
    assert d.net_info("SIG")["width_mm"] == extreme.EXTREME_TRACK_MM and d.net_info("RF")["width_mm"] == 0.8
    extreme._set_widths(d, {"SIG": None, "RF": 1.1})
    assert "width_mm" not in d.net_info("SIG") and d.net_info("RF")["width_mm"] == 1.1


def test_walled_pads_are_looked_for_only_when_no_pour_pad_is_tapped(tmp_path):
    """Step 3 taps the walled-in pour pads; a board that taps them already (board.pour_taps) or has no pour skips it
    before any DRC (the `check` callback is never called)."""
    d = _design(tmp_path)
    def check(*a):
        raise AssertionError("no DRC expected")
    d.raw["board"]["pour_taps"] = ["R4"]
    assert extreme._walled(d, tmp_path / "x.kicad_pcb", [], [], [], check) == set()
    d.raw["board"].pop("pour_taps")
    d.raw["board"]["gnd_pour"] = False
    assert extreme._walled(d, tmp_path / "x.kicad_pcb", [], [], [], check) == set()
