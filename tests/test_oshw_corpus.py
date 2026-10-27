"""Open-hardware cases of the layout corpus (tests/fixtures/layout_corpus/oshw_*, scripts/oshw_corpus.py): each has
its provenance (URL, commit, license, attribution), the source license file and a NOTICE entry, stays small, and is
listed in oshw/index.json. Their layout metrics run in tests/test_layout_corpus.py with the other cases."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "layout_corpus"
CASES = sorted(p for p in CORPUS.glob("oshw_*") if (p / "design.yaml").exists())
MAX_CASE_KB = 1500
# licenses whose terms allow the fixtures (attribution kept; share-alike ones keep the case under the same license)
LICENSES = {"CERN-OHL-P-2.0", "CERN-OHL-W-2.0", "CERN-OHL-S-2.0", "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause",
            "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0", "CC0-1.0", "GPL-3.0", "GPL-3.0-or-later",
            "GPL-2.0", "GPL-2.0-or-later", "TAPR-OHL-1.0", "CERN-OHL-1.2", "SHL-2.1", "BSD-1-Clause", "0BSD", "Unlicense", "ISC"}


def test_oshw_cases_exist():
    assert len(CASES) >= 10


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_provenance(case):
    e = yaml.safe_load((case / "expected.yaml").read_text())
    s = e["source"]
    assert s["url"].startswith("https://")
    assert re.fullmatch(r"[0-9a-f]{40}", s["commit"])
    assert s["license"] in LICENSES, s["license"]
    assert s.get("attribution")
    assert (case / "LICENSE.source").is_file()
    notice = (CORPUS / "NOTICE").read_text()
    assert case.name in notice and s["url"] in notice
    assert e["env"]["HWFLOW_FAST"] == "1"
    h = e["human"]["copper"]
    assert h["segments"] > 0


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_size(case):
    kb = sum(f.stat().st_size for f in case.rglob("*") if f.is_file()) / 1024
    assert kb <= MAX_CASE_KB, f"{case.name}: {kb:.0f} kB"


def test_index():
    idx = json.loads((ROOT / "oshw" / "index.json").read_text())
    rows = {r["name"]: r for r in idx["boards"]}
    for c in CASES:
        r = rows[c.name.removeprefix("oshw_")]
        assert r["license"] and r["url"] and r["layers"] in (2, 4) and len(r["size_mm"]) == 2
        assert isinstance(r["mpns"], list) and isinstance(r["blocks"], list) and isinstance(r["mcu"], list)
