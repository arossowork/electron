"""One typed settings object for every switch and path of the engine (docs/settings.md).

All `os.environ` reads of hwflow live here (tests/test_no_env_reads.py enforces it). The env var names are the ones the
engine always used. Settings are built at call time by `get_settings()`: nothing is frozen at import, so a test can
`monkeypatch.setenv(...)` and see the effect on the next call. Subprocess environments (`dict(os.environ, ...)` in
kicad.py, pcb.py, cli.py) pass the parent environment through to children on purpose; they are the allowlisted exception.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]  # the repository: blocks/, library/, tools/ (until packaged)
_OFF = ("0", "false", "no", "off")


def _off_words(v: Any) -> Any:
    """On unless one of 0, false, no, off (blank is on)."""
    return str(v).strip().lower() not in _OFF if isinstance(v, str) else v


def _not_zero(v: Any) -> Any:
    """On unless exactly "0"."""
    return v != "0" if isinstance(v, str) else v


OffWords = Annotated[bool, BeforeValidator(_off_words)]
NotZero = Annotated[bool, BeforeValidator(_not_zero)]


def _first_existing(cands, *, dir_with: str | None = None) -> str:
    for c in cands:
        if c and (Path(c) / dir_with if dir_with else Path(c)).exists():
            return str(c)
    return ""


class _Group(BaseSettings):
    # env names are given per field (alias); unknown env vars are ignored; fields are also settable by name
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True, env_prefix="")


class LayoutSettings(_Group):
    asic: dict[str, str] = Field(default_factory=dict)  # HWFLOW_ASIC_<NAME> stage switches, raw values by lower-case name

    flow: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_FLOW"))
    keepout_strict: OffWords = Field(True, validation_alias=AliasChoices("HWFLOW_KEEPOUT_STRICT"))
    neckdown: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_NECKDOWN"))
    seedroute: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_SEEDROUTE"))
    seed0: int = Field(1, validation_alias=AliasChoices("HWFLOW_SEED0"))
    budget_s: float = Field(3000.0, validation_alias=AliasChoices("HWFLOW_LAYOUT_BUDGET_S"))
    auto_max_errors: int | None = Field(None, validation_alias=AliasChoices("HWFLOW_AUTO_MAX_ERRORS"))

    @model_validator(mode="before")
    @classmethod
    def _asic(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("asic", {k[len("HWFLOW_ASIC_"):].lower(): v for k, v in os.environ.items()
                                     if k.startswith("HWFLOW_ASIC_")})
        return data

    def asic_on(self, name: str) -> bool:
        """Stage switch: HWFLOW_ASIC_<NAME>=0 (false, no, off) turns the stage off; all on by default."""
        v = self.asic.get(name.lower())
        return v is None or v.strip().lower() not in _OFF


class RepairSettings(_Group):
    max_errors: int = Field(40, validation_alias=AliasChoices("HWFLOW_REPAIR_MAX_ERRORS"))
    budget_s: float = Field(120.0, validation_alias=AliasChoices("HWFLOW_REPAIR_BUDGET_S"))
    max_stall: int = Field(2, validation_alias=AliasChoices("HWFLOW_REPAIR_MAX_STALL"))


class RouterSettings(_Group):
    # KICAD_* / KRT_* (except KICAD_CLI) reach the router subprocess and change its copper: they are cache-key inputs
    env_knobs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _knobs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("env_knobs", {k: v for k, v in sorted(os.environ.items())
                                          if k.startswith(("KICAD_", "KRT_")) and k != "KICAD_CLI"})
        return data


class ToolSettings(_Group):
    """External programs. Empty string: not found. Order: env var, PATH, then the old macOS locations."""
    kicad_cli: str = Field("", validation_alias=AliasChoices("KICAD_CLI"))
    java: str = Field("", validation_alias=AliasChoices("JAVA"))
    ngspice: str = Field("", validation_alias=AliasChoices("HWFLOW_NGSPICE"))
    openems_python: str = Field("", validation_alias=AliasChoices("HWFLOW_OPENEMS_PYTHON"))
    em_reexec: bool = Field(False, validation_alias=AliasChoices("HWFLOW_EM_REEXEC"))  # set by hwflow for its child
    em_verbose: int = Field(0, validation_alias=AliasChoices("HWFLOW_EM_VERBOSE"))

    @model_validator(mode="after")
    def _resolve(self):
        home = os.path.expanduser("~")
        mac_kicad = ("Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",)
        if not (self.kicad_cli and Path(self.kicad_cli).exists()):
            self.kicad_cli = _first_existing([shutil.which("kicad-cli"), f"{home}/{mac_kicad[0]}", f"/{mac_kicad[0]}"])
        if not self.java:
            self.java = _first_existing([shutil.which("java"), "/opt/homebrew/opt/openjdk/bin/java"]) or "java"
        if not self.ngspice:
            self.ngspice = shutil.which("ngspice") or "/opt/homebrew/bin/ngspice"
        if not self.openems_python:
            self.openems_python = str(Path.home() / "opt" / "openEMS" / "venv" / "bin" / "python")
        return self


class PathSettings(_Group):
    """Data dirs and bundled tools. Everything defaults to a place under `root` (the repository)."""
    root: str = Field(str(REPO_ROOT), validation_alias=AliasChoices("HWFLOW_ROOT"))
    blocks_dir: str = Field("", validation_alias=AliasChoices("HWFLOW_BLOCKS_DIR"))
    library_dir: str = Field("", validation_alias=AliasChoices("HWFLOW_LIBRARY_DIR"))
    tools_dir: str = Field("", validation_alias=AliasChoices("HWFLOW_TOOLS_DIR"))
    krt_dir: str = Field("", validation_alias=AliasChoices("KRT_DIR"))
    freerouting: str = Field("", validation_alias=AliasChoices("FREEROUTING"))
    kicad_share: str = Field("", validation_alias=AliasChoices("KICAD_SHARE"))  # holds symbols/ and footprints/

    @model_validator(mode="after")
    def _resolve(self):
        root = Path(self.root)
        self.blocks_dir = self.blocks_dir or str(root / "blocks")
        self.library_dir = self.library_dir or str(root / "library")
        self.tools_dir = self.tools_dir or str(root / "tools")
        self.krt_dir = self.krt_dir or str(Path(self.tools_dir) / "KiCadRoutingTools")
        self.freerouting = self.freerouting or str(Path(self.tools_dir) / "freerouting-2.5.0.jar")
        home = os.path.expanduser("~")
        # "" when KiCad's libraries are not found (libs.share_dir raises): env, Linux, then macOS
        self.kicad_share = _first_existing(
            [self.kicad_share, "/usr/share/kicad", "/usr/local/share/kicad",
             f"{home}/Applications/KiCad/KiCad.app/Contents/SharedSupport",
             "/Applications/KiCad/KiCad.app/Contents/SharedSupport"], dir_with="symbols")
        return self


class Settings(BaseModel):
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    repair: RepairSettings = Field(default_factory=RepairSettings)
    router: RouterSettings = Field(default_factory=RouterSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    paths: PathSettings = Field(default_factory=PathSettings)

    @model_validator(mode="after")
    def _derived(self):
        if self.layout.auto_max_errors is None:
            self.layout.auto_max_errors = self.repair.max_errors
        return self

    def snapshot(self) -> dict:
        """Resolved settings for status.json. No setting holds a secret today; any future one is dropped here."""
        return self.model_dump(mode="json")

    def key_dump(self) -> dict:
        """The part of the settings that changes the copper or the layout (cache keys): not tool or data paths."""
        return self.model_dump(mode="json", include={"layout", "repair", "router"})


def get_settings() -> Settings:
    """Settings from the environment now. Cheap and uncached on purpose: tests change the environment."""
    return Settings()
