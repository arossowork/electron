"""hwflow fw: board header from design.yaml + interface.yaml (docs/hwsw-codesign.md)."""

import copy
import re
from pathlib import Path

import pytest
import yaml

from hwflow import fw
from hwflow.design import Design

PROJ = Path(__file__).resolve().parents[1] / "projects" / "home-thermostat-3"

# Pin names as `hwflow lib show` prints them (thermo:ESP32-C3-MINI-1, Analog_ADC:ADS1115IDGS, Driver_Motor:DRV8837),
# so the tests do not need a KiCad installation.
_ESP = {"1": "GND", "2": "GND", "3": "3V3", "5": "IO2", "6": "IO3", "8": "EN", "11": "GND", "12": "IO0", "13": "IO1",
        "14": "GND", "16": "IO10", "18": "IO4", "19": "IO5", "20": "IO6", "21": "IO7", "22": "IO8", "23": "IO9",
        "26": "IO18", "27": "IO19", "30": "IO20/RXD0", "31": "IO21/TXD0", **{str(n): "GND" for n in range(36, 54)}}
_ADS = {"1": "ADDR", "2": "ALERT/RDY", "3": "GND", "4": "AIN0", "5": "AIN1", "6": "AIN2", "7": "AIN3", "8": "VDD",
        "9": "SDA", "10": "SCL"}
_DRV = {"1": "VM", "2": "OUT1", "3": "OUT2", "4": "GND", "5": "IN2", "6": "IN1", "7": "~{SLEEP}", "8": "VCC", "9": "GND"}


class FakeLibs:
    SYMS = {"thermo:ESP32-C3-MINI-1": _ESP, "Analog_ADC:ADS1115IDGS": _ADS, "Driver_Motor:DRV8837": _DRV}

    def symbol(self, lib_id):
        return {"pins": [{"number": k, "name": v} for k, v in self.SYMS[lib_id].items()]}


@pytest.fixture
def design():
    return Design.load(PROJ)


@pytest.fixture
def contract():
    return yaml.safe_load((PROJ / "interface.yaml").read_text())


def gen(d, c, tmp_path):
    p = tmp_path / "interface.yaml"
    p.write_text(yaml.safe_dump(c))
    return fw.generate(d, FakeLibs(), p)


def define(text, name):
    m = re.search(rf"^#define {name}\s+(\S+)", text, re.M)
    return m.group(1) if m else None


def errors(findings):
    return [f for f in findings if f["severity"] == "error"]


def test_thermostat_header(design, contract, tmp_path):
    text, findings = gen(design, contract, tmp_path)
    assert errors(findings) == []
    # GPIO numbers from the symbol pin names of the pins design.yaml uses
    assert define(text, "BOARD_GPIO_VBAT_EN") == "2"
    assert define(text, "BOARD_GPIO_SENS_EN") == "4"
    assert define(text, "BOARD_GPIO_K1_IN1") == "7" and define(text, "BOARD_GPIO_K1_IN2") == "10"
    assert define(text, "BOARD_GPIO_K2_IN1") == "20" and define(text, "BOARD_GPIO_K2_IN2") == "21"
    assert define(text, "BOARD_ADC1_CH_VBAT_ADC") == "3"
    assert define(text, "BOARD_VBAT_EN_ACTIVE") == "0" and define(text, "BOARD_BTN_ACTIVE") == "0"
    # derived from part values and straps
    assert define(text, "BOARD_VBAT_DIV_RATIO") == "3.0f"
    assert define(text, "BOARD_I2C_ADDR_ADS") == "0x48"
    assert define(text, "BOARD_ADS_AIN_REF") == "0" and define(text, "BOARD_ADS_AIN_NTC2") == "2"
    # relay window from R9: 2 x 15 ms .. 9 mJ x 40.5 ohm / 3.418^2
    assert define(text, "BOARD_RELAY_PULSE_MIN_US") == "30000"
    assert define(text, "BOARD_RELAY_PULSE_MAX_US") == "31200"
    assert define(text, "BOARD_RELAY_MIN_DWELL_S") == "8640"
    # the 25 C table entry is exactly half scale (Rfix = R25)
    table = re.search(r"BOARD_NTC_TABLE_Q16\[[A-Z_]+\] = \{(.*?)\};", text, re.S).group(1)
    vals = [int(v) for v in re.findall(r"\d+", table)]
    assert len(vals) == 101 and vals[45] == 32768 and vals == sorted(vals, reverse=True)
    # every constant line has a source comment
    for line in text.splitlines():
        if line.startswith("#define BOARD_") and line.split()[1] not in ("BOARD_H", "BOARD_PROJECT", "BOARD_REVISION"):
            assert "/*" in line, line
    assert "#error" not in text
    # the one open number is reported, not invented
    assert any(f["code"] == "open" and "vbat_warn_mv" in f["message"] for f in findings)
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
    assert any(f["code"] == "contract-missing-signal" and "NEW_SIG" in f["message"] for f in findings)
    assert "#error" in text


def test_stale_contract_signal_is_an_error(design, contract, tmp_path):
    c = copy.deepcopy(contract)
    c["signals"]["GONE"] = {"dir": "out"}
    _, findings = gen(design, c, tmp_path)
    assert any(f["code"] == "contract-stale-signal" for f in findings)


def test_divider_ratio_and_topology(design, contract, tmp_path):
    design.components["R16"].data["value"] = "20k"
    text, findings = gen(design, contract, tmp_path)
    assert define(text, "BOARD_VBAT_DIV_RATIO") == "2.0f" and errors(findings) == []
    design.components["R16"].data["pins"] = {"1": "VBAT_ADC", "2": "+3V3"}
    _, findings = gen(design, contract, tmp_path)
    assert any(f["code"] == "divider" for f in findings)


def test_i2c_address_from_strap(design, contract, tmp_path):
    design.components["U3"].data["pins"]["1"] = "SENS_EN"     # ADDR to the device VDD
    text, _ = gen(design, contract, tmp_path)
    assert define(text, "BOARD_I2C_ADDR_ADS") == "0x49"
    design.components["U3"].data["pins"]["1"] = "+3V3"        # not in the address map
    _, findings = gen(design, contract, tmp_path)
    assert any(f["code"] == "i2c-addr" for f in findings)


def test_relay_binding_checked_against_driver_symbol(design, contract, tmp_path):
    c = copy.deepcopy(contract)
    c["relays"][0]["in1"], c["relays"][0]["in2"] = "K1_IN2", "K1_IN1"
    _, findings = gen(design, c, tmp_path)
    assert sum(f["code"] == "relay-pin" for f in findings) == 2


def test_pulse_outside_derived_window(design, contract, tmp_path):
    """A SW timing the HW cannot meet is an error that goes back to the circuit stage."""
    c = copy.deepcopy(contract)
    c["timing"]["relay_pulse_us"]["value"] = 32000
    _, findings = gen(design, c, tmp_path)
    assert any(f["code"] == "relay-window" and f["severity"] == "error" for f in findings)


def test_no_contract(design, tmp_path):
    text, findings = fw.generate(design, FakeLibs(), tmp_path / "missing.yaml")
    assert text == "" and findings[0]["code"] == "no-contract"


def test_real_libraries(design):
    """Same result with the KiCad symbols (skipped without a KiCad library install)."""
    from hwflow.libs import Libs
    try:
        libs = Libs(design=design)
        libs.symbol("Driver_Motor:DRV8837")
    except (FileNotFoundError, KeyError):
        pytest.skip("KiCad symbol libraries not installed")
    text, findings = fw.generate(design, libs)
    assert errors(findings) == [] and define(text, "BOARD_GPIO_K2_IN2") == "21"
