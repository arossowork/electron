"""`hwflow em`: optional openEMS run."""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.design import Design
from ..core.settings import get_settings
from ..pipeline.report import _fmt
from ..pipeline.status import _save_status


def cmd_em(a, argv) -> int:
    """openEMS lives in its own venv (built from source). Re-execute there when it is not importable here."""
    import os
    from ..verify import em
    if not em.available():
        ts = get_settings().tools
        py = Path(ts.openems_python)
        if not py.exists() or ts.em_reexec:
            print("em: openEMS Python bindings not found (see docs/em-openems-spike.md); skipped")
            return 0
        # subprocess environment: the child inherits the parent environment, with the re-exec guard set
        env = dict(os.environ, HWFLOW_EM_REEXEC="1",
                   PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")]))
        import subprocess
        return subprocess.call([str(py), "-m", "hwflow.cli"] + list(argv if argv is not None else sys.argv[1:]), env=env)
    d = Design.load(a.project)
    if not em.em_nets(d) and not a.net:
        print("em: no net has `em:` set in design.yaml; nothing to do")
        return 0
    res = em.run_project(d, a.net, a.fmax_ghz, a.cell_mm)
    for r in res["nets"]:
        s = {x["f_ghz"]: x for x in r["s_at"]}
        s1 = s.get(1.0) or r["s_at"][-1]
        print(f"em {r['net']} {r['ports'][0]}->{r['ports'][1]} {r['length_mm']} mm: Z_tdr {_fmt(r['z_tdr_plateau'])} ohm "
              f"(closed form microstrip {r['z0_closed_form']}, CPWG {_fmt(r['z0_cpwg_closed_form_lenavg'])}), "
              f"@{s1['f_ghz']} GHz S11 {s1['s11_db']} dB S21 {s1['s21_db']} dB; "
              f"{r['runtime_s']:.0f} s, {r['cells']} cells, {r['peak_rss_mb']:.0f} MB"
              + ("" if r["ok"] else f"  FAIL vs target {r['z_target']} ohm"))
    _save_status(d, "em", res)
    return 0 if res["ok"] else 1
