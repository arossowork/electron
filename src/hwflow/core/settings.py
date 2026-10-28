"""One typed settings object for every switch and path of the engine (docs/settings.md).

All `os.environ` reads of hwflow live here (tests/test_no_env_reads.py enforces it). The env var names are the ones the
engine always used. Settings are built at call time by `get_settings()`: nothing is frozen at import, so a test can
`monkeypatch.setenv(...)` and see the effect on the next call. Subprocess environments (`dict(os.environ, ...)` in
kicad.py, pcb.py, commands/em.py) pass the parent environment through to children on purpose; they are the allowlisted exception.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]  # the repository: blocks/, library/, tools/ (until packaged)
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


# HWFLOW_FAST=1: layout fields and their values under the preset (only where the field is not given explicitly)
FAST_PRESET: dict[str, Any] = {"pour_stitch": True, "place_escape": "1", "roads": "route"}


class LayoutSettings(_Group):
    asic: dict[str, str] = Field(default_factory=dict)  # HWFLOW_ASIC_<NAME> stage switches, raw values by lower-case name

    flow: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_FLOW"))
    keepout_strict: OffWords = Field(True, validation_alias=AliasChoices("HWFLOW_KEEPOUT_STRICT"))
    neckdown: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_NECKDOWN"))
    seedroute: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_SEEDROUTE"))
    seed0: int = Field(1, validation_alias=AliasChoices("HWFLOW_SEED0"))
    budget_s: float = Field(3000.0, validation_alias=AliasChoices("HWFLOW_LAYOUT_BUDGET_S"))
    auto_max_errors: int | None = Field(None, validation_alias=AliasChoices("HWFLOW_AUTO_MAX_ERRORS"))
    settle: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_SETTLE"))  # Placer.settle legalizer
    # incremental (ECO) placement from build/placement.good.json after a clean layout (layout/place/eco.py)
    eco: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_ECO"))
    route_illegal: bool = Field(False, validation_alias=AliasChoices("HWFLOW_ROUTE_ILLEGAL"))  # debugging only
    routab_predict: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_ROUTAB_PREDICT"))
    # raw values; None: the design's placement.routability_<name>, then off (routability.py)
    routab_rank: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_ROUTAB_RANK"))
    routab_feedback: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_ROUTAB_FEEDBACK"))
    routab_retry: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_ROUTAB_RETRY"))
    # roads first (layout.route.roads): 0, 1 (reservations + roads routed first), route (roads routed first);
    # None: the design's placement.roads, then off
    roads: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_ROADS"))
    roads_power: bool = Field(False, validation_alias=AliasChoices("HWFLOW_ROADS_POWER"))  # every power net a road
    # escape-aware placement (layout.place.escape, prototype): 1 = escape slot term in the detailed moves and the
    # anneal; None: the design's placement.escape, then off. place_escape_infl / _rot: steps 2 and 3 (same values)
    place_escape: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_PLACE_ESCAPE"))
    place_escape_infl: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_PLACE_ESCAPE_INFL"))
    place_escape_rot: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_PLACE_ESCAPE_ROT"))
    place_escape_w: float = Field(10.0, validation_alias=AliasChoices("HWFLOW_PLACE_ESCAPE_W"))
    place_escape_conn: float = Field(1.0, validation_alias=AliasChoices("HWFLOW_PLACE_ESCAPE_CONN"))
    # placer terms from the design's own rules (docs/layout-flow.md "Budgets and block copper in placement"):
    # block copper keeps the isolation and class distances like a pad; a copper_len budget pulls its pads together
    # (off: HWFLOW_PLACE_BLOCK_COPPER=0 / HWFLOW_PLACE_BUDGETS=0)
    place_block_copper: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_PLACE_BLOCK_COPPER"))
    place_budgets: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_PLACE_BUDGETS"))
    place_budget_w: float = Field(40.0, validation_alias=AliasChoices("HWFLOW_PLACE_BUDGET_W"))
    # decap_max_mm (component, else board) as a hard placement limit of each `decouples:` cap's supply pad to the
    # nearest IC pad on that net (a distance leg of Placer._init_budget_pairs). Off by default: on
    # desktop-dac-hd6xx (41 movable decaps) all 4 attempts kept 1-3 caps 4.3-5.7 mm from their pin, so none was
    # routed; without it attempt 1 placed legally and routed (4 open, 4 DRC in the 3000 s budget). =1 turns it on
    place_decap_hard: NotZero = Field(False, validation_alias=AliasChoices("HWFLOW_PLACE_DECAP_HARD"))
    # placement.edge as a hard rule: a movable part's courtyard more than placer.EDGE_TOL off its edge is a violation
    place_edge_hard: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_PLACE_EDGE_HARD"))
    # board.antenna_clearance_mm / thermal_clearance_mm written in design.yaml as hard placement rules (the isolation
    # weight and a violation); the defaults (10 / 5 mm) stay soft
    place_sep_hard: NotZero = Field(True, validation_alias=AliasChoices("HWFLOW_PLACE_SEP_HARD"))
    # routed copper over the straight pad distance a copper_len budget leg is placed for (hard limit = max_mm / it):
    # thermo-e2e-1 tvs_ntc1 routed 5.82 mm between pads 4.70 mm apart (1.24), 5.46 in the live run
    place_budget_detour: float = Field(1.25, validation_alias=AliasChoices("HWFLOW_PLACE_BUDGET_DETOUR"))
    krt_rotate: int = Field(0, validation_alias=AliasChoices("HWFLOW_KRT_ROTATE"))  # first router net order
    # pour stitch (layout.route.pourstitch): join a split GND pour after routing with vias in the fill, vias in
    # exposed pads, pad stubs to a via, and stubs that displace routed nets; off by default, on with
    # HWFLOW_POUR_STITCH=1
    pour_stitch: bool = Field(False, validation_alias=AliasChoices("HWFLOW_POUR_STITCH"))
    # pour taps (layout.route.taps): before signal routing, every surface-mount pad of a pour net gets a link or a stub
    # and via to the plane, kept as fixed copper; off by default, on with HWFLOW_POUR_TAPS=1 (measurements in
    # docs/layout-flow.md, step 7)
    pour_taps: bool = Field(False, validation_alias=AliasChoices("HWFLOW_POUR_TAPS"))
    # routable first pass (layout.route.firstpass, experiment, off by default): "1" (every step) or a comma list of
    # roads (planned roads reserved in placement and routed first), hot (the routability window cuts as a local
    # placer penalty), quick (a truncated route as a filter that drives one local placement fix); None: the
    # design's placement.firstpass, then off
    firstpass: str | None = Field(None, validation_alias=AliasChoices("HWFLOW_FIRSTPASS"))
    firstpass_quick_s: float = Field(240.0, validation_alias=AliasChoices("HWFLOW_FIRSTPASS_QUICK_S"))
    firstpass_hot_w: float = Field(4.0, validation_alias=AliasChoices("HWFLOW_FIRSTPASS_HOT_W"))
    # layout portfolio (pipeline.portfolio): off by default, on with HWFLOW_PORTFOLIO=1
    portfolio: bool = Field(False, validation_alias=AliasChoices("HWFLOW_PORTFOLIO"))
    portfolio_workers: int = Field(0, validation_alias=AliasChoices("HWFLOW_PORTFOLIO_WORKERS"))  # 0: cpu count - 2
    portfolio_grace: float = Field(1.5, validation_alias=AliasChoices("HWFLOW_PORTFOLIO_GRACE"))
    # fast preset (docs/layout-flow.md "Fast preset"): HWFLOW_FAST=1 turns on the switches that measured faster to a
    # clean first placement: pour_stitch, place_escape=1 (step 1 only) and roads=route (roads preseeded only, no
    # reservations in placement). A switch given explicitly
    # (env or field) wins over the preset. Off by default.
    fast: bool = Field(False, validation_alias=AliasChoices("HWFLOW_FAST"))

    @model_validator(mode="before")
    @classmethod
    def _asic(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("asic", {k[len("HWFLOW_ASIC_"):].lower(): v for k, v in os.environ.items()
                                     if k.startswith("HWFLOW_ASIC_")})
        return data

    @model_validator(mode="after")
    def _fast(self):
        if self.fast:
            for name, value in FAST_PRESET.items():
                if name not in self.model_fields_set:
                    setattr(self, name, value)
        return self

    def asic_on(self, name: str) -> bool:
        """Stage switch: HWFLOW_ASIC_<NAME>=0 (false, no, off) turns the stage off; all on by default."""
        v = self.asic.get(name.lower())
        return v is None or v.strip().lower() not in _OFF


class RepairSettings(_Group):
    max_errors: int = Field(40, validation_alias=AliasChoices("HWFLOW_REPAIR_MAX_ERRORS"))
    # a safety abort only (repair.py): the repair and the pour-stitch re-routes stop on counts
    budget_s: float = Field(1800.0, validation_alias=AliasChoices("HWFLOW_REPAIR_BUDGET_S"))
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
    cc: str = Field("", validation_alias=AliasChoices("CC"))  # C compiler of `hwflow fw test`; "" = gcc
    ngspice: str = Field("", validation_alias=AliasChoices("HWFLOW_NGSPICE"))
    openems_python: str = Field("", validation_alias=AliasChoices("HWFLOW_OPENEMS_PYTHON"))
    em_reexec: bool = Field(False, validation_alias=AliasChoices("HWFLOW_EM_REEXEC"))  # set by hwflow for its child
    em_verbose: int = Field(0, validation_alias=AliasChoices("HWFLOW_EM_VERBOSE"))
    # /hw-design stage time budgets, "3-parts=40,7-layout=60" (pipeline.budget, docs/stage-budget.md); not a cache key
    stage_budgets: str = Field("", validation_alias=AliasChoices("HWFLOW_STAGE_BUDGETS"))

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
    cas_dir: str = Field("", validation_alias=AliasChoices("HWFLOW_CAS_DIR"))  # LocalCAS root and the stage cache
    # reference designs (`hwflow ref search`, core/refs.py): folders of designs (one design.yaml per sub-folder,
    # os.pathsep-separated) and the external open-hardware index (JSON; optional, missing is fine)
    ref_dirs: str = Field("", validation_alias=AliasChoices("HWFLOW_REF_DIRS"))
    oshw_index: str = Field("", validation_alias=AliasChoices("HWFLOW_OSHW_INDEX"))

    @model_validator(mode="after")
    def _resolve(self):
        root = Path(self.root)
        self.blocks_dir = self.blocks_dir or str(root / "blocks")
        self.library_dir = self.library_dir or str(root / "library")
        self.tools_dir = self.tools_dir or str(root / "tools")
        self.krt_dir = self.krt_dir or str(Path(self.tools_dir) / "KiCadRoutingTools")
        self.freerouting = self.freerouting or str(Path(self.tools_dir) / "freerouting-2.5.0.jar")
        self.cas_dir = self.cas_dir or str(Path(os.path.expanduser("~")) / ".cache" / "hwflow" / "cas")
        self.ref_dirs = self.ref_dirs or os.pathsep.join([str(root / "examples"), str(root / "projects")])
        self.oshw_index = self.oshw_index or str(root / "oshw" / "index.json")
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
        return self.model_dump(mode="json", include=set(KEY_GROUPS))

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> Settings:
        """The key groups (layout, repair, router) of a run's snapshot; tools and paths from the local environment, so a
        stage run on another machine uses that machine's binaries and data dirs (ADR 0001, 2.5)."""
        env = cls()
        groups = {g: type(getattr(env, g)).model_validate(snapshot[g]) for g in KEY_GROUPS if g in (snapshot or {})}
        return env.model_copy(update=groups)


KEY_GROUPS = ("layout", "repair", "router")
_CURRENT: ContextVar[Settings | None] = ContextVar("hwflow_settings", default=None)
_PROCESS: list[Settings] = []  # the same, for threads the block starts (a context variable does not reach a pool)


@contextmanager
def use(settings: Settings) -> Iterator[Settings]:
    """Make `get_settings()` return `settings` inside the block (a stage run under its run's snapshot), also in the
    threads started meanwhile (the SPICE runs of a stage use a thread pool)."""
    tok = _CURRENT.set(settings)
    _PROCESS.append(settings)
    try:
        yield settings
    finally:
        _PROCESS.remove(settings)
        _CURRENT.reset(tok)


def with_layout_env(settings: Settings, env: Mapping[str, Any]) -> Settings:
    """`settings` with the layout knobs of `env` (env var names, as HWFLOW_ROADS=route) applied over its layout group;
    names that are no layout setting are ignored. A layout portfolio strategy runs under it (pipeline.portfolio)."""
    fields = {}
    for name, f in LayoutSettings.model_fields.items():
        for alias in getattr(f.validation_alias, "choices", None) or ():
            if alias in env:
                fields[name] = env[alias]
    if not fields:
        return settings
    base = settings.layout.model_dump()
    if "fast" in fields:  # the preset fills its switches again unless `env` gives them
        base = {k: v for k, v in base.items() if k not in FAST_PRESET}
    layout = LayoutSettings(**{**base, **fields})
    return settings.model_copy(update={"layout": layout})


def override() -> Settings | None:
    """The settings `use()` set (in this context or a thread it started), or None when they come from the environment."""
    cur = _CURRENT.get()
    if cur is not None:
        return cur
    return _PROCESS[-1] if _PROCESS else None


@lru_cache(maxsize=8)
def _env_blocks_dir(blocks_dir: str, root: str) -> str:  # the key: the environment variables blocks_dir comes from
    return Settings().paths.blocks_dir


def blocks_dir() -> str:
    """paths.blocks_dir, cheap: the placer asks for it thousands of times (blocks.library_root), and building the
    settings each time took a third of a floorplan run. From the environment it is cached on the two variables it
    depends on (HWFLOW_BLOCKS_DIR, HWFLOW_ROOT); under `use()` it is that snapshot's."""
    cur = override()
    if cur is not None:
        return cur.paths.blocks_dir
    return _env_blocks_dir(os.environ.get("HWFLOW_BLOCKS_DIR", ""), os.environ.get("HWFLOW_ROOT", ""))


def get_settings() -> Settings:
    """Settings from the environment now, or the ones `use()` set. Cheap and uncached on purpose: tests change the
    environment."""
    cur = _CURRENT.get()
    if cur is not None:
        return cur
    return _PROCESS[-1] if _PROCESS else Settings()
