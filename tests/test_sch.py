"""Generated schematics keep all placed content on their drawing sheet."""

import pytest
import yaml

from hwflow import sch, sexpr
from hwflow.design import Design
from hwflow.libs import Libs


def _design(tmp_path, count, ground_count=1):
    raw = {
        "project": "sheet_size",
        "board": {"width_mm": 30, "height_mm": 20, "layers": 2},
        "nets": {f"GND{i}": {"class": "ground"} for i in range(ground_count)},
        "components": {
            f"R{i}": {
                "value": "10k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                "pins": {"1": f"SIGNAL_{i}", "2": f"GND{i % ground_count}"},
            }
            for i in range(count)
        },
    }
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def _assert_content_inside_page(tree, libs):
    paper = sexpr.find(tree, "paper")
    width, height = (420, 297) if paper[1] == "A3" else map(float, paper[2:4])
    for item in tree:
        if not isinstance(item, list) or not item:
            continue
        at = sexpr.find(item, "at")
        if at is None:
            continue
        x, y = map(float, at[1:3])
        assert 0 <= x <= width and 0 <= y <= height
        if item[0] == "symbol":
            symbol = libs.symbol(str(sexpr.value(item, "lib_id")))
            unit = int(sexpr.value(item, "unit"))
            for prop in sexpr.find_all(item, "property"):
                effects = sexpr.find(prop, "effects")
                if sexpr.find(effects, "hide"):
                    continue
                px, py = map(float, sexpr.find(prop, "at")[1:3])
                field_width = 1.27 * len(prop[2])
                justify = sexpr.find(effects, "justify") or []
                left, right = (px, px + field_width) if "left" in justify else (px - field_width / 2, px + field_width / 2)
                assert 0 <= left <= right <= width
                assert 0 <= py - 1.27 <= py + 1.27 <= height
            for pin in symbol["pins"]:
                if pin["unit"] in (0, unit):
                    assert 0 <= x + pin["x"] <= width
                    assert 0 <= y - pin["y"] <= height
        elif item[0] == "global_label":
            length = 1.27 * 0.8 * len(item[1]) + 3.0
            dx, dy = {0: (length, 0), 90: (0, -length), 180: (-length, 0), 270: (0, length)}[int(at[3])]
            assert 0 <= x + dx <= width
            assert 0 <= y + dy <= height


def test_small_schematic_keeps_a3_sheet(tmp_path):
    d, libs = _design(tmp_path, 3)
    tree = sch.build_schematic(d, libs)
    assert sexpr.find(tree, "paper") == ["paper", "A3"]
    _assert_content_inside_page(tree, libs)


@pytest.mark.parametrize("count,ground_count", [(160, 1), (50, 50)])
def test_large_schematic_sheet_contains_every_component_and_label(tmp_path, count, ground_count):
    d, libs = _design(tmp_path, count, ground_count)
    tree = sch.build_schematic(d, libs)
    paper = sexpr.find(tree, "paper")
    assert paper[1] == "User"
    assert float(paper[2]) > 420 or float(paper[3]) > 297
    assert len(sexpr.find_all(tree, "symbol")) == count + ground_count
    _assert_content_inside_page(tree, libs)
