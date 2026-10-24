"""hwflow fw: contract checks and generated headers (docs/hwsw-codesign.md). One tiny generic design, then the
thermostat as the worked example."""

import copy
import re
from pathlib import Path

import pytest
import yaml

from hwflow import fw
from hwflow.design import Design

PROJ = Path(__file__).resolve().parents[1] / "projects" / "home-thermostat-3"

# Pin names as `hwflow lib show` prints them, so the tests need no KiCad installation.
_ESP = {"1": "GND", "2": "GND", "3": "3V3", "5": "IO2", "6": "IO3", "8": "EN", "11": "GND", "12": "IO0", "13": "IO1",
        "14": "GND", "16": "IO10", "18": "IO4", "19": "IO5", "20": "IO6", "21": "IO7", "22": "IO8", "23": "IO9",
        "26": "IO18", "27": "IO19", "30": "IO20/RXD0", "31": "IO21/TXD0", **{str(n): "GND" for n in range(36, 54)}}
_ADS = {"1": "ADDR", "2": "ALERT/RDY", "3": "GND", "4": "AIN0", "5": "AIN1", "6": "AIN2", "7": "AIN3", "8": "VDD",
        "9": "SDA", "10": "SCL"}
_DRV = {"1": "VM", "2": "OUT1", "3": "OUT2", "4": "GND", "5": "IN2", "6": "IN1", "7": "~{SLEEP}", "8": "VCC", "9": "GND"}
_SHT = {"1": "SDA", "2": "SCL", "3": "VDD", "4": "VSS"}
_TWO = {"1": "~", "2": "~"}


class FakeLibs:
    SYMS = {"thermo:ESP32-C3-MINI-1": _ESP, "RF_Module:ESP32-C3-MINI-1": _ESP, "Analog_ADC:ADS1115IDGS": _ADS,
            "Driver_Motor:DRV8837": _DRV, "Sensor_Humidity:SHT4x": _SHT, "Device:R": _TWO, "Device:LED": _TWO,
            "Switch:SW_Push": _TWO}

    def symbol(self, lib_id):
        return {"pins": [{"number": k, "name": v} for k, v in self.SYMS[lib_id].items()]}


def define(text, name):
    m = re.search(rf"^#define {name}\s+(\S+)", text, re.M)
    return m.group(1) if m else None


def errors(findings):
    return [f for f in findings if f["severity"] == "error"]


def codes(findings):
    return {f["code"] for f in findings}


# ------------------------------------------------------------------ a tiny non-thermostat design
R = {"symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric", "tolerance": 0.01}
TINY = {
    "project": "plant_logger",
    "nets": {"VBAT": {"class": "power"}, "+3V3": {"class": "power"}, "GND": {"class": "ground"}},
    "components": {
        "U1": {"value": "ESP32-C3-MINI-1", "symbol": "RF_Module:ESP32-C3-MINI-1", "footprint": "x:y",
               "pins": {"1": "GND", "3": "+3V3", "6": "VBAT_ADC", "18": "BTN", "19": "SDA", "20": "SCL",
                        "21": "LED"}},
        "U2": {"value": "SHT40", "symbol": "Sensor_Humidity:SHT4x", "footprint": "x:y",
               "pins": {"1": "SDA", "2": "SCL", "3": "+3V3", "4": "GND"}},
        "R1": {**R, "value": "1k", "pins": {"1": "LED", "2": "LED_A"}},
        "D1": {"value": "LED", "symbol": "Device:LED", "footprint": "x:y", "pins": {"1": "GND", "2": "LED_A"}},
        "R2": {**R, "value": "10k", "pins": {"1": "+3V3", "2": "BTN"}},
        "SW1": {"value": "SW", "symbol": "Switch:SW_Push", "footprint": "x:y", "pins": {"1": "BTN", "2": "GND"}},
        "R3": {**R, "value": "100k", "pins": {"1": "VBAT", "2": "VBAT_ADC"}},
        "R4": {**R, "value": "100k", "pins": {"1": "VBAT_ADC", "2": "GND"}},
        "R5": {**R, "value": "4.7k", "pins": {"1": "+3V3", "2": "SDA"}},
        "R6": {**R, "value": "4.7k", "pins": {"1": "+3V3", "2": "SCL"}},
    },
}
TINY_IF = {
    "version": 1,
    "mcu": {"ref": "U1", "power_nets": ["+3V3"]},
    "signals": {"VBAT_ADC": {"dir": "adc"}, "BTN": {"dir": "in", "active": "low", "pull": "R2", "wake": "low"},
                "SDA": {"dir": "i2c"}, "SCL": {"dir": "i2c"}, "LED": {"dir": "out", "active": "high"}},
    "dividers": [{"name": "VBAT", "net": "VBAT_ADC", "top": "R3", "bottom": "R4"}],
    "buses": [{"kind": "i2c", "name": "env", "sda": "SDA", "scl": "SCL", "pullups": ["R5", "R6"],
               "devices": [{"ref": "U2", "name": "SHT", "addr": 0x44, "source": "SHT4x datasheet"}]}],
    "params": {"sample_s": {"value": 600}},
    "limits": {"report_s": {"value": "6 * sample_s", "min": "sample_s"}},
    "events": {"SAMPLE": {"a": "centi_c", "b": "centi_rh"}, "BUTTON": {"isr": True}},
    "modules": {"sense": {"doc": "reads the SHT40", "emits": ["SAMPLE"]},
                "ui": {"doc": "button and LED", "consumes": ["SAMPLE", "BUTTON"], "owns": ["LED"]}},
}


def tiny(tmp_path, design=None, contract=None):
    d = tmp_path / "plant"
    d.mkdir(exist_ok=True)
    (d / "design.yaml").write_text(yaml.safe_dump(design or TINY, sort_keys=False))
    (d / "interface.yaml").write_text(yaml.safe_dump(contract or TINY_IF, sort_keys=False))
    return Design.load(d)


def test_tiny_design_generic(tmp_path):
    files, findings = fw.generate(tiny(tmp_path), FakeLibs())
    assert errors(findings) == [], findings
    b = files["board.h"]
    assert define(b, "BOARD_MCU_FAMILY_ESP32C3") == "1"
    assert define(b, "BOARD_GPIO_BTN") == "4" and define(b, "BOARD_BTN_WAKE_LEVEL") == "0"
    assert define(b, "BOARD_GPIO_LED") == "7" and define(b, "BOARD_LED_ACTIVE") == "1"
    assert define(b, "BOARD_ADC_VBAT_ADC") == "3"
    assert define(b, "BOARD_VBAT_DIV_RATIO") == "2.0f"
    assert define(b, "BOARD_I2C_ADDR_SHT") == "0x44"
    assert define(b, "BOARD_REPORT_S") == "3600" and define(b, "BOARD_REPORT_S_MIN") == "600"
    assert "thermo" not in b.lower() and "relay" not in b.lower()
    ev = files["events.h"]
    assert "EV_SAMPLE = FWK_EV_USER + 0" in ev and "ev_sample(int32_t centi_c, int32_t centi_rh)" in ev
    assert re.search(r"#define SUBS_UI \(\(1ull << EV_SAMPLE\) \| \(1ull << EV_BUTTON\)\)", ev)
    assert "int mod_sense_task(fwk_task_t *t, const fwk_event_t *e);" in files["modules.h"]


def test_tiny_button_on_non_wake_pin(tmp_path):
    d = copy.deepcopy(TINY)
    p = d["components"]["U1"]["pins"]
    p["23"] = p.pop("18")                              # BTN to IO9: not RTC, and a strapping pin
    _, findings = fw.generate(tiny(tmp_path, design=d), FakeLibs())
    assert any(f["code"] == "wake-pin" and f["severity"] == "error" for f in findings)
    assert "strap" in codes(findings)


def test_tiny_adc_on_non_adc_pin(tmp_path):
    d = copy.deepcopy(TINY)
    p = d["components"]["U1"]["pins"]
    p["16"] = p.pop("6")                               # VBAT_ADC to IO10: no ADC1 channel
    _, findings = fw.generate(tiny(tmp_path, design=d), FakeLibs())
    assert "adc-pin" in codes(errors(findings))


def test_tiny_event_and_owner_checks(tmp_path):
    c = copy.deepcopy(TINY_IF)
    c["modules"]["sense"]["owns"] = ["LED"]            # two owners
    c["modules"]["ui"]["consumes"].append("NOPE")
    c["events"]["ORPHAN"] = {}
    _, findings = fw.generate(tiny(tmp_path, contract=c), FakeLibs())
    assert {"shared-pin", "event-unknown", "event-no-source"} <= codes(errors(findings))


def test_tiny_limit_below_min(tmp_path):
    c = copy.deepcopy(TINY_IF)
    c["limits"]["report_s"]["value"] = 60
    _, findings = fw.generate(tiny(tmp_path, contract=c), FakeLibs())
    assert "limit" in codes(errors(findings))


def test_unknown_family(tmp_path):
    d = copy.deepcopy(TINY)
    d["components"]["U1"].update(value="STM32G031", symbol="MCU_ST:STM32G031")
    _, findings = fw.generate(tiny(tmp_path, design=d), FakeLibs())
    assert "mcu-family" in codes(errors(findings))


def test_scaffold_never_overwrites(tmp_path):
    d = tiny(tmp_path)
    fw_dir = d.dir / "fw"
    written = fw.scaffold(d, fw_dir)
    names = {p.relative_to(fw_dir).as_posix() for p in written}
    assert {"modules/sense.c", "modules/ui.c", "app.c", "Makefile", "test/test_app.c"} <= names
    assert "case EV_BUTTON:" in (fw_dir / "modules" / "ui.c").read_text()
    (fw_dir / "app.c").write_text("/* mine */\n")
    assert fw.scaffold(d, fw_dir) == [] and (fw_dir / "app.c").read_text() == "/* mine */\n"


# ------------------------------------------------------------------ the thermostat (worked example)
@pytest.fixture
def design():
    return Design.load(PROJ)


@pytest.fixture
def contract():
    return yaml.safe_load((PROJ / "interface.yaml").read_text())


def gen(d, c, tmp_path):
    p = tmp_path / "interface.yaml"
    p.write_text(yaml.safe_dump(c))
    files, findings = fw.generate(d, FakeLibs(), p)
    return files.get("board.h", ""), findings


def test_thermostat_header(design, contract, tmp_path):
    text, findings = gen(design, contract, tmp_path)
    assert errors(findings) == []
    assert define(text, "BOARD_GPIO_VBAT_EN") == "2" and define(text, "BOARD_GPIO_SENS_EN") == "4"
    assert define(text, "BOARD_GPIO_K1_IN1") == "7" and define(text, "BOARD_GPIO_K1_IN2") == "10"
    assert define(text, "BOARD_GPIO_K2_IN1") == "20" and define(text, "BOARD_GPIO_K2_IN2") == "21"
    assert define(text, "BOARD_ADC_VBAT_ADC") == "3"
    assert define(text, "BOARD_VBAT_DIV_RATIO") == "3.0f" and define(text, "BOARD_I2C_ADDR_ADS") == "0x48"
    assert define(text, "BOARD_ADS_IN_REF") == "0" and define(text, "BOARD_ADS_IN_NTC2") == "2"
    # R9 window: 2 x 15 ms .. 9 mJ x 40.5 ohm / 3.418^2; D19 dwell and the watchdog it sets
    assert define(text, "BOARD_RELAY_PULSE_US_MIN") == "30000"
    assert define(text, "BOARD_RELAY_MIN_DWELL_S") == "2400" and define(text, "BOARD_RELAY_MAX_PULSES_PER_DAY") == "72"
    assert define(text, "BOARD_WDT_TIMEOUT_MS") == "1536" and define(text, "BOARD_SENSOR_ON_US") == "187500"
    table = re.search(r"BOARD_NTC_TABLE_Q16\[\d+\] = \{(.*?)\};", text, re.S).group(1)
    vals = [int(v) for v in re.findall(r"\d+", table)]
    assert len(vals) == 101 and vals[45] == 32768 and vals == sorted(vals, reverse=True)
    for line in text.splitlines():
        if line.startswith("#define BOARD_") and line.split()[1] not in ("BOARD_H", "BOARD_PROJECT", "BOARD_REVISION"):
            assert "/*" in line, line
    assert "#error" not in text
    # open numbers and the pending HW change are reported, not invented
    assert any(f["code"] == "open" and "vbat_warn_mv" in f["message"] for f in findings)
    assert any(f["code"] == "wake-pin" and f["severity"] == "warning" for f in findings)
    assert define(text, "BOARD_VBAT_WARN_MV") is None


def test_pin_moved_in_layout_follows(design, contract, tmp_path):
    """A pin swap in design.yaml changes board.h without touching the contract."""
    u1 = design.components["U1"].data["pins"]
    u1["21"], u1["22"] = u1["22"], u1["21"]          # K1_IN1 <-> LED_N
    text, findings = gen(design, contract, tmp_path)
    assert errors(findings) == []
    assert define(text, "BOARD_GPIO_K1_IN1") == "8" and define(text, "BOARD_GPIO_LED_N") == "7"


def test_new_mcu_net_without_contract_is_an_error(design, contract, tmp_path):
    design.components["U1"].data["pins"]["24"] = "NEW_SIG"
    text, findings = gen(design, contract, tmp_path)
    assert "contract-missing-signal" in codes(findings) and "#error" in text


def test_stale_contract_signal_is_an_error(design, contract, tmp_path):
    c = copy.deepcopy(contract)
    c["signals"]["GONE"] = {"dir": "out"}
    _, findings = gen(design, c, tmp_path)
    assert "contract-stale-signal" in codes(findings)


def test_divider_ratio_and_topology(design, contract, tmp_path):
    design.components["R16"].data["value"] = "20k"
    text, findings = gen(design, contract, tmp_path)
    assert define(text, "BOARD_VBAT_DIV_RATIO") == "2.0f" and errors(findings) == []
    design.components["R16"].data["pins"] = {"1": "VBAT_ADC", "2": "+3V3"}
    _, findings = gen(design, contract, tmp_path)
    assert "divider" in codes(findings)


def test_i2c_address_from_strap(design, contract, tmp_path):
    design.components["U3"].data["pins"]["1"] = "SENS_EN"     # ADDR to the device VDD
    text, _ = gen(design, contract, tmp_path)
    assert define(text, "BOARD_I2C_ADDR_ADS") == "0x49"
    design.components["U3"].data["pins"]["1"] = "+3V3"        # not in the address map
    _, findings = gen(design, contract, tmp_path)
    assert "i2c-addr" in codes(findings)


def test_binding_checked_against_device_symbol(design, contract, tmp_path):
    c = copy.deepcopy(contract)
    p = c["bindings"][0]["pins"]
    p["IN1"], p["IN2"] = p["IN2"], p["IN1"]
    _, findings = gen(design, c, tmp_path)
    assert sum(f["code"] == "binding" for f in findings) == 2


def test_pulse_outside_derived_window(design, contract, tmp_path):
    """A SW timing the HW cannot meet is an error that goes back to the circuit stage."""
    c = copy.deepcopy(contract)
    c["limits"]["relay_pulse_us"]["value"] = 32000
    _, findings = gen(design, c, tmp_path)
    assert "limit" in codes(errors(findings))


def test_no_contract(design, tmp_path):
    files, findings = fw.generate(design, FakeLibs(), tmp_path / "missing.yaml")
    assert files == {} and findings[0]["code"] == "no-contract"


def test_real_libraries(design):
    """Same result with the KiCad symbols (skipped without a KiCad library install)."""
    from hwflow.libs import Libs
    try:
        libs = Libs(design=design)
        libs.symbol("Driver_Motor:DRV8837")
    except (FileNotFoundError, KeyError):
        pytest.skip("KiCad symbol libraries not installed")
    files, findings = fw.generate(design, libs)
    assert errors(findings) == [] and define(files["board.h"], "BOARD_GPIO_K2_IN2") == "21"


def test_scaffold_builds_and_runs_on_the_framework(tmp_path):
    """Generated headers + scaffolded modules compile against fw/framework and run in the host harness."""
    import shutil
    import subprocess
    if not shutil.which("gcc"):
        pytest.skip("gcc not installed")
    d = tiny(tmp_path)
    files, _ = fw.generate(d, FakeLibs())
    gen_dir = d.dir / "build" / "fw"
    gen_dir.mkdir(parents=True)
    for name, text in files.items():
        (gen_dir / name).write_text(text)
    fw_dir = d.dir / "fw"
    fw.scaffold(d, fw_dir)
    fwk = fw.repo_root() / "fw" / "framework"
    exe = tmp_path / "app_tests"
    src = [fwk / "src" / "fwk.c", fwk / "test" / "fwk_sim.c", *sorted((fw_dir / "modules").glob("*.c")),
           fw_dir / "app.c", fw_dir / "test" / "test_app.c"]
    cmd = ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-const-variable", f"-I{fwk / 'include'}",
           f"-I{fwk / 'test'}", f"-I{gen_dir}", f"-I{fw_dir}", "-o", str(exe), *map(str, src)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    assert subprocess.run([str(exe)], capture_output=True).returncode == 0
