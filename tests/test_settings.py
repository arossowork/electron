"""Settings: env names, defaults, call-time reads; and the guard that env reads live in settings.py only."""
import re
from pathlib import Path

from hwflow.layout.place import asicflow
from hwflow.layout.board import keepout
from hwflow.core.settings import get_settings

SRC = Path(__file__).resolve().parents[1] / "src" / "hwflow"
# subprocess environments that pass the parent environment through to a child on purpose (file: how many)
# commands/run.py: HWFLOW_EXECUTOR and the detached child's environment; pipeline/runner.py: HWFLOW_RUN_ID (process control, not settings)
ALLOWED = {"layout/board/kicad.py": 1, "layout/route/krt.py": 1, "commands/em.py": 2, "commands/run.py": 2, "pipeline/runner.py": 1}


def test_defaults(monkeypatch):
    for k in ("HWFLOW_FLOW", "HWFLOW_REPAIR_MAX_ERRORS", "HWFLOW_REPAIR_BUDGET_S", "HWFLOW_AUTO_MAX_ERRORS",
              "HWFLOW_LAYOUT_BUDGET_S", "HWFLOW_KEEPOUT_STRICT", "HWFLOW_NECKDOWN", "KRT_DIR", "HWFLOW_ROOT"):
        monkeypatch.delenv(k, raising=False)
    s = get_settings()
    assert (s.repair.max_errors, s.repair.budget_s, s.repair.max_stall) == (40, 120.0, 2)
    assert s.layout.budget_s == 3000 and s.layout.auto_max_errors == 40 and s.layout.seed0 == 1
    assert s.layout.flow is None and s.layout.keepout_strict and s.layout.neckdown and s.layout.seedroute
    assert s.paths.krt_dir.endswith("tools/KiCadRoutingTools") and Path(s.paths.blocks_dir).name == "blocks"


def test_env_read_at_call_time(monkeypatch):
    monkeypatch.setenv("HWFLOW_REPAIR_BUDGET_S", "7")
    assert get_settings().repair.budget_s == 7
    monkeypatch.setenv("HWFLOW_REPAIR_BUDGET_S", "9")
    assert get_settings().repair.budget_s == 9
    monkeypatch.setenv("HWFLOW_ASIC_NECK", "off")
    assert not asicflow.opt("neck") and asicflow.opt("dp")
    monkeypatch.setenv("HWFLOW_FLOW", "Legacy")
    assert asicflow.flow() == "legacy"
    monkeypatch.setenv("HWFLOW_KEEPOUT_STRICT", "no")
    assert not keepout.enabled()
    monkeypatch.setenv("HWFLOW_NECKDOWN", "false")  # only "0" turns it off
    assert get_settings().layout.neckdown


def test_root_moves_data_dirs(monkeypatch, tmp_path):
    # Explicit paths take precedence over the root, including the test image's KRT_DIR.
    for key in ("HWFLOW_BLOCKS_DIR", "HWFLOW_TOOLS_DIR", "KRT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HWFLOW_ROOT", str(tmp_path))
    p = get_settings().paths
    assert p.blocks_dir == str(tmp_path / "blocks") and p.krt_dir == str(tmp_path / "tools" / "KiCadRoutingTools")


def test_key_dump_follows_env(monkeypatch):
    monkeypatch.delenv("KICAD_RIP_PREEXISTING", raising=False)
    a = get_settings().key_dump()
    monkeypatch.setenv("KICAD_RIP_PREEXISTING", "1")
    assert get_settings().key_dump() != a


def test_no_env_reads_outside_settings():
    bad = []
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        if rel == "core/settings.py":
            continue
        n = len(re.findall(r"os\.environ|os\.getenv|\bgetenv\(", "\n".join(
            line for line in f.read_text().splitlines() if not line.lstrip().startswith("#"))))
        if n != ALLOWED.get(rel, 0):
            bad.append((rel, n, ALLOWED.get(rel, 0)))
    assert not bad, f"os.environ outside settings.py (file, found, allowed): {bad}"


def test_from_snapshot_and_use(monkeypatch, tmp_path):
    """A stage runs under its run's settings snapshot (key groups), with tools and paths from this machine; threads
    started inside the block see it too (ADR 0001, 2.5)."""
    import concurrent.futures as cf

    from hwflow.core import settings as sm
    monkeypatch.setenv("HWFLOW_SEED0", "7")
    snap = get_settings().snapshot()
    monkeypatch.setenv("HWFLOW_SEED0", "9")
    monkeypatch.setenv("HWFLOW_CAS_DIR", str(tmp_path / "cas"))
    s = sm.Settings.from_snapshot(snap)
    assert s.layout.seed0 == 7 and s.paths.cas_dir == str(tmp_path / "cas")
    with sm.use(s):
        assert get_settings().layout.seed0 == 7
        with cf.ThreadPoolExecutor(1) as ex:
            assert ex.submit(lambda: get_settings().layout.seed0).result() == 7
    assert get_settings().layout.seed0 == 9


def test_cas_dir_default(monkeypatch):
    monkeypatch.delenv("HWFLOW_CAS_DIR", raising=False)
    assert get_settings().paths.cas_dir.endswith(".cache/hwflow/cas")
