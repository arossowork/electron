#!/usr/bin/env python
"""Open-hardware KiCad boards as layout test cases (tests/fixtures/layout_corpus/oshw_*) and a searchable index.

    uv run python scripts/oshw_corpus.py fetch [NAME ...]    shallow clones of oshw/sources.yaml into oshw/src/<name>
    uv run python scripts/oshw_corpus.py import [NAME ...]   the corpus case of each board, with the human reference
    uv run python scripts/oshw_corpus.py index               oshw/index.json (name, URL, license, MCU, blocks, MPNs,
                                                             layers, size) from the cloned boards
    then  scripts/layout_corpus.py measure oshw_<name> --fast --write   (fast tier)
          scripts/layout_corpus.py measure oshw_<name> --route --write  (slow tier; the case's `env` applies)

The clones are untrusted data: nothing in them is executed (no scripts, makefiles or plugins); only the .kicad_pcb,
.kicad_pro, .kicad_dru, .kicad_sch and license files are read. oshw/src/ and oshw/work/ are not committed.

`import` runs `hwflow.layout.board.kicad_import.import_board` (the board's own placement, all parts fixed, its net
classes as net widths and clearances) and writes into the case's expected.yaml:
- `source`: URL, commit, file, license, attribution, OSHWA id; the license file is copied next to the case;
- `env`: HWFLOW_FAST=1 (the baselines are measured with the fast preset) and HWFLOW_ROUTE_ILLEGAL=1 when the
  source placement breaks the placer's legality rules (overlapping courtyards a human accepted): a routing benchmark
  of the human placement, not a placement verdict;
- `human`: the board's own copper (track length per layer, segments, vias), KiCad DRC of the source board under its
  own rules (`drc_source_rules`), and of the human copper written on the imported board under hwflow's rules
  (`drc_hwflow_rules`: the same writer, rules and DRC as a routed case, so it is the reference for `route`).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OSHW = ROOT / "oshw"
SOURCES = OSHW / "sources.yaml"
SRC = OSHW / "src"
WORK = OSHW / "work"
CORPUS = ROOT / "tests" / "fixtures" / "layout_corpus"
PREFIX = "oshw_"


def sources(names=None) -> list[dict]:
    s = yaml.safe_load(SOURCES.read_text()) or []
    return [x for x in s if not names or x["name"] in names]


def git(*args, cwd=None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600,
                       env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin", "HOME": str(OSHW)})
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def fetch(s: dict) -> Path:
    """Shallow clone (no hooks run on clone; submodules and LFS are not fetched)."""
    dst = SRC / s["name"]
    if not (dst / ".git").exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--no-recurse-submodules", "-c", "core.hooksPath=/dev/null"]
        if s.get("branch"):
            args += ["--branch", s["branch"]]
        git(*args, s["url"], str(dst))
    return dst


def commit(s: dict) -> str:
    return git("-C", str(SRC / s["name"]), "rev-parse", "HEAD")


def _errors(viol: list[dict]) -> dict:
    c = Counter(v["type"] for v in viol if v["sev"] == "error")
    return {"errors": sum(n for t, n in c.items() if t != "unconnected"), "unconnected": c.pop("unconnected", 0),
            "types": dict(sorted(c.items()))}


def human_reference(s: dict, case: Path, pcb: Path) -> dict:
    """The human layout as a reference: its copper, DRC under the source rules and under hwflow's."""
    from hwflow.core.design import Design
    from hwflow.core.geometry import cu_layers
    from hwflow.core.libs import Libs
    from hwflow.layout.board import kicad, kicad_import, pcb as pcbw
    from hwflow.layout.place import placer

    rec = yaml.safe_load((case / "cutout.yaml").read_text())
    ren = dict(rec.get("net_renames") or {})
    cop = kicad_import.human_copper(pcb, rec["offset"], ren)
    out = {"copper": kicad_import.copper_metrics(cop)}
    w = WORK / s["name"]
    shutil.rmtree(w, ignore_errors=True)
    (w / "orig").mkdir(parents=True)
    shutil.copy(pcb, w / "orig" / "board.kicad_pcb")
    for ext in (".kicad_pro", ".kicad_dru"):
        if pcb.with_suffix(ext).exists():
            shutil.copy(pcb.with_suffix(ext), w / "orig" / f"board{ext}")
    try:
        out["drc_source_rules"] = _errors(kicad.drc(w / "orig" / "board.kicad_pcb", refill=False,
                                                    keepout_strict=False))
    except Exception as e:  # noqa: BLE001 - recorded, the case stands without it
        out["drc_source_rules"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    d = Design.load(case / "design.yaml")
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    for r, p in parts.items():
        pl = d.components[r].get("placement") or {}
        p.x, p.y, p.r = float(pl["x"]), float(pl["y"]), float(pl.get("rot", 0))
    known = set(d.nets())
    tracks = [t for t in cop["tracks"] if t["net"] in known and t["layer"] in cu_layers(int(d.board["layers"]))]
    vias = [v for v in cop["vias"] if v["net"] in known]
    (w / "human").mkdir()
    tree = pcbw.build_board(d, libs, parts, tracks=tracks, vias=vias)
    # the human copper as drawn: each via at its own size (the writer uses the board via), the board's own zones
    # (power planes and rule areas) instead of the writer's ground pour
    from hwflow.core.sexpr import Q
    vnodes = [x for x in tree if isinstance(x, list) and x and x[0] == "via"]
    for node, v in zip(vnodes, vias):
        if v.get("size") and v.get("drill"):
            node[2], node[3] = ["size", v["size"]], ["drill", v["drill"]]
    zones = kicad_import.human_zones(pcb, rec["offset"], ren)
    if zones:
        netidx = {str(x[2]): x[1] for x in tree if isinstance(x, list) and x and x[0] == "net" and len(x) > 2}
        tree = [x for x in tree if not (isinstance(x, list) and x and x[0] == "zone")]
        for z in zones:
            node = list(z["zone"])
            if z["net"] in netidx:
                node[1:1] = [["net", netidx[z["net"]]], ["net_name", Q(z["net"])]]
            tree.append(node)
    pcbw.write_board(w / "human" / "board.kicad_pcb", tree)
    pcbw.write_project(w / "human", "board", d)
    try:
        out["drc_hwflow_rules"] = _errors(kicad.drc(w / "human" / "board.kicad_pcb"))
    except Exception as e:  # noqa: BLE001
        out["drc_hwflow_rules"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return out


def import_one(s: dict) -> dict:
    from hwflow.layout.board import kicad_import
    from hwflow.layout.place import placer
    from hwflow.core.design import Design
    from hwflow.core.libs import Libs

    src = SRC / s["name"]
    pcb = src / s["pcb"]
    case = CORPUS / f"{PREFIX}{s['name']}"
    if case.exists():
        shutil.rmtree(case)
    sha = commit(s)
    source = {"url": s["url"], "commit": sha, "file": s["pcb"], "license": s["license"],
              "attribution": s.get("attribution"), "oshwa": s.get("oshwa"), "category": s.get("category")}
    rec = kicad_import.import_board(pcb, case, name=s["name"], source=source,
                                    pro=src / s["pro"] if s.get("pro") else None)
    lic = s.get("license_file")
    if lic and (src / lic).is_file():
        shutil.copy(src / lic, case / "LICENSE.source")
    elif s.get("license_text"):  # the license is stated in the README only
        (case / "LICENSE.source").write_text(f"{s['url']}\n{s['license_text']}\n"
                                             f"License text: https://spdx.org/licenses/{s['license']}.html\n")
    d = Design.load(case / "design.yaml")
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    viol = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"]).violations()
    env = {"HWFLOW_FAST": "1"}
    if viol:
        env["HWFLOW_ROUTE_ILLEGAL"] = "1"
    exp = {"source": source, "kind": "oshw board (whole board, source placement, tracks stripped)",
           "env": env, "human": human_reference(s, case, pcb)}
    (case / "expected.yaml").write_text(yaml.safe_dump(exp, sort_keys=False, width=120, allow_unicode=True))
    return {"case": case.name, "parts": len(d.components), "nets": len(d.nets()), "board": rec["board_mm"],
            "layers": d.board["layers"], "violations": len(viol), "warnings": len(rec["warnings"]),
            "human": exp["human"]}


# ------------------------------------------------------------------ index

MCU_RE = [(r"ESP32-?C3", "ESP32-C3"), (r"ESP32-?C6", "ESP32-C6"), (r"ESP32-?S3", "ESP32-S3"), (r"ESP32-?S2", "ESP32-S2"),
          (r"ESP32-?H2", "ESP32-H2"), (r"ESP32", "ESP32"), (r"ESP8266|ESP-?12|ESP-?01|ESP8285", "ESP8266"),
          (r"nRF52\d*|E73-", "nRF52"), (r"nRF53\d*", "nRF53"), (r"nRF54\w*", "nRF54"), (r"RP2040", "RP2040"),
          (r"RP2350", "RP2350"), (r"STM32\w*", "STM32"), (r"ATMEGA\w*", "ATmega"), (r"ATTINY\w*", "ATtiny"),
          (r"ATSAMD\w*|SAMD\d\d", "SAMD"), (r"CH32V\w*", "CH32V"), (r"CH552|CH55\d", "CH55x"), (r"GD32\w*", "GD32"),
          (r"PIC1[0268]F\w+|PIC24\w+|PIC32\w+|DSPIC\w+", "PIC"), (r"MSP430\w*", "MSP430"), (r"EFR32\w*", "EFR32"), (r"CC13\d\d|CC26\d\d", "TI CC13xx/26xx"),
          (r"BL602|BL702", "Bouffalo"), (r"W806|W801", "Winner Micro"), (r"ICE40\w*", "iCE40 FPGA"),
          (r"ECP5|LFE5\w*", "ECP5 FPGA"), (r"PY32\w*", "PY32"), (r"LPC\d{3,4}\w*", "LPC"),
          (r"ARDUINO|PRO_?MINI|NANO_?V3", "Arduino module"), (r"RASPBERRY_?PI_?(ZERO|40|GPIO)|RPI_?ZERO|RPI_GPIO|PI_?ZERO", "Raspberry Pi header")]
BLOCKS = [  # (block, regex over value/footprint/MPN/lib id of a part, or over net names when the key starts with net:)
    ("relay", r"^RELAY|G5LE|HF\d{2}F|SRD-\d\dVDC|HFD\d|JQC|OMRON G\d|PANASONIC ALQ|ALQ\d|G6K|HF115F|RELAY_SPDT|RELAY_SPST"),
    ("ssr_triac", r"MOC30\d\d|BT13\d|BTA\d\d|TRIAC|AQH\d|G3MB|CPC1\d{3}|ASSR|S202S"),
    ("ac_dc_module", r"HLK-?(PM|5M|10M|2M)|IRM-\d\d|RAC\d\d|AC-?DC|LNK3\d\d|VIPER\d\d|TNY\d{3}|KP3\d\d"),
    ("mains_net", r"net:^(AC_?L|AC_?N|L_?IN|N_?IN|LINE|NEUTRAL|MAINS\w*|230V\w*|120V\w*|VAC\w*|L|N)$"),
    ("fuse", r"FUSE|^F\d|POLYFUSE|PTC"), ("varistor", r"VARISTOR|MOV|^S\d\dK\d+|^\d+D\d+K"),
    ("optocoupler", r"PC817|LTV-?\d|EL817|TLP\d+|6N13\d|OPTO"),
    ("usb_c", r"USB_?C|TYPE-?C|USB4\d{3}|HRO|TYPE_C"), ("usb_micro", r"MICRO_?USB|USB_MICRO|MICROUSB"),
    ("usb_pd", r"CH224|FUSB302|STUSB4500|IP2721|HUSB238|TPS6598|AP33772|CYPD\d"),
    ("usb_uart", r"CH340|CH343|CH9102|CP210\d|FT232|FT231|PL2303"),
    ("lipo_charger", r"TP4056|TP4054|MCP7383\d|BQ2\d{3}|LTC4054|MAX1555|IP5306|SGM4056|BQ25\d{3}|XC6802"),
    ("fuel_gauge", r"MAX170\d\d|BQ27\d{3}|LC709\d+"), ("battery", r"BATTERY|LIPO|18650|CR20\d\d|BT\d|JST_PH"),
    ("buck", r"TPS5\d{3}|TPS6\d{4}|MP1584|MP2\d{3}|AP63\d\d|LMR\d{5}|SY8\d{3}|RT8\d{3}|LM2596|XL1509|MT2492|AOZ\d{4}|TLV62\d{3}|MP15\d\d"),
    ("boost", r"TPS61\d{3}|MT3608|SX1308|XL6009|MCP1640|TLV61\d+"),
    ("ldo", r"AMS1117|AP2112|XC6206|XC6220|MCP1700|MCP1702|LP5907|HT7333|HT7833|RT9013|TLV7\d{4}|ME6211|LD1117|SPX3819|AP7333|LM1117|NCP1117|MIC5\d{3}|TPS7A\d+"),
    ("crystal", r"CRYSTAL|XTAL|^Y\d|ABM\d|TSX-3225|FC-135"),
    ("antenna", r"ANTENNA|U\.?FL|IPEX|SMA_EDGE|CONN_SMA|SMA-?J|COAX|CHIP_ANT|2450AT"),
    ("lora_radio", r"SX127\d|SX126\d|RFM9\d|RA-0\d|E22-|LLCC68|STM32WL"),
    ("rs485_can", r"MAX485|MAX3485|SN65HVD|SP3485|MCP2551|TJA10\d\d|MCP2562|ISO1050"),
    ("adc", r"ADS1\d{3}|MCP3\d{3}|ADS12\d\d|AD7\d{3}|HX711|NAU7802|MAX1120\d|LTC24\d\d|ADS131"),
    ("dac", r"MCP47\d\d|DAC\d{4}|AD56\d\d|PCM51\d\d"),
    ("biopotential_afe", r"AD8232|ADS129\d|MAX3000\d"),
    ("opamp", r"OPA\d{3,4}|LM358|LM324|MCP600\d|TL07\d|NE5532|AD86\d\d|LMV3\d\d|TLV9\d{3}|ADA4\d{3}|INA\d{3}|AD62\d"),
    ("audio", r"PCM\d{4}|WM8\d{3}|MAX98357|TPA\d{4}|CS4\d{3}|SGTL5000|ES8\d{3}|AUDIO_JACK|PJ-3\d\d"),
    ("current_sense", r"INA2\d\d|INA1\d\d|ACS7\d\d|INA3\d\d|MAX4080|HLW8\d\d\d|BL0937|CSE77\d\d|ADE7\d{3}"),
    ("env_sensor", r"BME\d{3}|BMP\d{3}|SHT[234]\d|SHTC\d|HDC\d{4}|AHT\d\d|SCD4\d|SGP\d\d|DS18B20|TMP\d{3}|SI70\d\d|LPS\d\d|ENS\d{3}"),
    ("imu", r"MPU-?\d{4}|LSM\d|BMI\d{3}|ICM-?\d{5}|LIS\dDH|BNO0\d\d|ADXL\d{3}"),
    ("display", r"OLED|SSD13\d\d|ST77\d\d|EPAPER|E-INK|ILI9\d{3}|LCD"),
    ("led_driver", r"WS2812|SK6812|APA102|IS31FL\d+|TLC59\d+"),
    ("motor_driver", r"DRV8\d{3}|A4988|TMC2\d{3}|L298|TB6612|DRV88"),
    ("mosfet_switch", r"AO3400|AO3401|SI2302|IRLML|BSS138|DMG\d{4}|2N7002"),
    ("esd", r"USBLC6|TPD\dE|PRTR5V|ESD\w*|SRV05|TVS|SMAJ|SMBJ|PESD"),
    ("terminal_block", r"TERMINAL|SCREW_TERMINAL|TERMINALBLOCK|PHOENIX|KF\d{3}|DG\d{3}|WAGO"),
    ("ethernet", r"W5500|LAN87\d\d|KSZ\d{4}|RJ45|HR911105|ENC28J60"),
    ("sd_card", r"MICRO_?SD|SD_CARD|TF_CARD|MICROSD"), ("button", r"^SW\d|BUTTON|TACT|SKQG|PTS\d{3}"),
    ("eeprom_flash", r"W25Q\d+|AT24C\d+|24LC\d+|GD25Q\d+|IS25\w+|AT25\w+|PY25Q"),
]


def scan_board(pcb: Path) -> dict:
    """Parts (ref, value, footprint, MPN fields), nets, layers and size of a board, for the index."""
    from hwflow.core import sexpr
    from hwflow.layout.board import kicad_import as ki
    t = sexpr.loads(pcb.read_text(encoding="utf-8", errors="replace"))
    parts = []
    for fp in sexpr.find_all(t, "footprint"):
        p = ki._props(fp)
        if not sexpr.find_all(fp, "pad"):
            continue
        mpn = next((p[k] for k in ki.PART_PROPS["mpn"] if p.get(k) and p[k] not in ("~", "-")), None)
        lcsc = next((p[k] for k in ki.PART_PROPS["lcsc"] if p.get(k) and p[k] not in ("~", "-")), None)
        man = next((p[k] for k in ki.PART_PROPS["manufacturer"] if p.get(k) and p[k] not in ("~", "-")), None)
        parts.append({"ref": p.get("Reference", "?"), "value": p.get("Value", ""), "footprint": str(fp[1]),
                      "mpn": mpn, "lcsc": lcsc, "manufacturer": man,
                      "side": "back" if str(sexpr.value(fp, "layer", "F.Cu")) == "B.Cu" else "front"})
    shape, (x0, y0, x1, y1) = ki.outline(t)
    nets = sorted({n for n in ki._net_table(t).values() if n})
    if not nets:  # KiCad 10 boards keep net names on the pads
        nets = sorted({str(sexpr.find(p, "net")[-1]) for fp in sexpr.find_all(t, "footprint")
                       for p in sexpr.find_all(fp, "pad") if sexpr.find(p, "net")})
    return {"parts": parts, "nets": nets, "layers": len(ki.copper_layers(t)),
            "size_mm": [round(x1 - x0, 1), round(y1 - y0, 1)], "area_mm2": round(shape.area, 1),
            "format": int(str(sexpr.value(t, "version", "0")))}


def scan_schematics(pcb: Path) -> list[dict]:
    """Symbols of the .kicad_sch files next to the board (all sheets): reference, value, lib id, part-number fields."""
    from hwflow.core import sexpr
    from hwflow.layout.board import kicad_import as ki
    out = []
    for f in sorted(pcb.parent.glob("*.kicad_sch")):
        try:
            t = sexpr.loads(f.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        for sym in sexpr.find_all(t, "symbol"):  # placed symbols (lib_symbols holds the library copies)
            lib = sexpr.value(sym, "lib_id")
            if lib is None:
                continue
            p = {str(x[1]): str(x[2]) for x in sexpr.find_all(sym, "property") if len(x) > 2}
            pick = (lambda keys: next((p[k] for k in keys if p.get(k) and p[k] not in ("~", "-")), None))
            out.append({"ref": p.get("Reference", "?"), "value": p.get("Value", ""), "lib_id": str(lib),
                        "mpn": pick(ki.PART_PROPS["mpn"]), "lcsc": pick(ki.PART_PROPS["lcsc"]),
                        "manufacturer": pick(ki.PART_PROPS["manufacturer"])})
    return out


def detect(scan: dict) -> tuple[list[str], list[str]]:
    texts = [" ".join(str(x or "") for x in (p["value"], p["footprint"], p["mpn"], p.get("lib_id"))).upper()
             for p in scan["parts"]]
    mcus = []
    for rx, name in MCU_RE:
        if any(re.search(rx, t, re.I) for t in texts) and name not in mcus:
            if name == "ESP32" and any(m.startswith("ESP32-") for m in mcus):
                continue
            mcus.append(name)
    blocks = []
    for name, rx in BLOCKS:
        if rx.startswith("net:"):
            r = re.compile(rx[4:], re.I)
            hit = any(r.match(n.split("/")[-1]) for n in scan["nets"])
        else:
            r = re.compile(rx, re.I)
            hit = any(r.search(t) or r.search(p["ref"]) for t, p in zip(texts, scan["parts"])) if name in (
                "fuse", "crystal", "button", "battery") else any(r.search(t) for t in texts)
        if hit:
            blocks.append(name)
    return mcus, blocks


def index(names=None) -> list[dict]:
    rows = []
    for s in sources(names):
        pcb = SRC / s["name"] / s["pcb"]
        if not pcb.exists():
            print(f"{s['name']}: not fetched")
            continue
        scan = scan_board(pcb)
        sch = scan_schematics(pcb)
        byref = {p["ref"]: p for p in sch}
        for p in scan["parts"]:  # board parts with their schematic fields
            q = byref.get(p["ref"]) or {}
            p["lib_id"] = q.get("lib_id")
            for k in ("mpn", "lcsc", "manufacturer"):
                p[k] = p[k] or q.get(k)
        mcus, blocks = detect(scan)
        case = CORPUS / f"{PREFIX}{s['name']}"
        exp = yaml.safe_load((case / "expected.yaml").read_text()) if (case / "expected.yaml").exists() else {}
        mpns = sorted({p["mpn"] for p in scan["parts"] if p["mpn"]})
        rows.append({"name": s["name"], "title": s.get("title") or s["name"], "url": s["url"],
                     "commit": (exp.get("source") or {}).get("commit") or commit(s), "file": s["pcb"],
                     "license": s["license"], "attribution": s.get("attribution"), "oshwa": s.get("oshwa"),
                     "category": s.get("category"), "description": s.get("description"),
                     "mcu": mcus, "blocks": blocks, "mpns": mpns,
                     "lcsc": sorted({p["lcsc"] for p in scan["parts"] if p["lcsc"]}),
                     "key_parts": sorted({p["value"] for p in scan["parts"]
                                          if re.match(r"^(U|IC|Q|K|PS|MOD)\d", p["ref"]) and p["value"]}),
                     "layers": scan["layers"], "size_mm": scan["size_mm"], "area_mm2": scan["area_mm2"],
                     "parts": len(scan["parts"]), "back_side_parts": sum(p["side"] == "back" for p in scan["parts"]),
                     "nets": len(scan["nets"]), "kicad_format": scan["format"],
                     "corpus_case": case.name if case.exists() else None,
                     "human_copper": (exp.get("human") or {}).get("copper")})
    OSHW.mkdir(exist_ok=True)
    (OSHW / "index.json").write_text(json.dumps({"version": 1, "boards": rows}, indent=1) + "\n")
    return rows


NOTICE_HEAD = """Third-party open-hardware designs in this corpus
================================================

The cases named oshw_* are derived from the open-hardware KiCad projects listed below. Each case holds data
extracted from the project's .kicad_pcb (component placement, pad-to-net connections, footprints as placed on the
board, net-class rules, board outline) by scripts/oshw_corpus.py (hwflow.layout.board.kicad_import). Changes from
the source: the tracks, vias and zones are removed; net names are rewritten (sheet paths and punctuation as `_`);
the schematic symbols are replaced by generated pin boxes; component values of R, C and L are cut to their
number; 3D models and footprints without pads are dropped. Each case keeps the source license file as
LICENSE.source, and its expected.yaml `source` names the URL, the commit, the license and the authors. A case is
distributed under the license of its source (share-alike licenses: the same license applies to the case).

"""


def notice() -> Path:
    rows = []
    for case in sorted(CORPUS.glob(f"{PREFIX}*")):
        f = case / "expected.yaml"
        if not f.exists():
            continue
        s = (yaml.safe_load(f.read_text()) or {}).get("source") or {}
        rows.append(f"{case.name}\n  source:  {s.get('url')} (commit {s.get('commit')}, file {s.get('file')})\n"
                    f"  license: {s.get('license')}\n  authors: {s.get('attribution')}\n"
                    + (f"  OSHWA:   {s['oshwa']}\n" if s.get("oshwa") else ""))
    out = CORPUS / "NOTICE"
    out.write_text(NOTICE_HEAD + "\n".join(rows))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["fetch", "import", "index"])
    ap.add_argument("names", nargs="*")
    a = ap.parse_args(argv)
    if a.what == "index":
        rows = index(a.names)
        print(f"{len(rows)} boards -> {OSHW / 'index.json'}")
        return 0
    bad = 0
    for s in sources(a.names):
        try:
            if a.what == "fetch":
                fetch(s)
                print(f"{s['name']}: {commit(s)[:10]}")
            else:
                r = import_one(s)
                print(json.dumps(r))
        except Exception as e:  # noqa: BLE001 - one board's failure is reported, the others go on
            bad += 1
            print(f"{s['name']}: {type(e).__name__}: {e}")
    if a.what == "import":
        print(f"notice: {notice()}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
