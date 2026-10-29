"""`hwflow fw test <proj>`: build and run the firmware host tests with gcc (docs/hwsw-codesign.md, firmware stage).

The recipe is the one `hwflow fw --scaffold` writes into `fw/Makefile`, without make, uv or the repository layout,
so it runs the same on a developer machine and in a hosted worker (where the agent has no shell):

1. regenerate the contract headers `build/fw/{board,events,modules}.h` from design.yaml and interface.yaml
   (a contract error stops here);
2. compile the framework (`fw/framework/src/fwk.c`, `test/fwk_sim.c`) and every `*.c` under the project's `fw/`
   except `fw/port/` (the SDK ports do not build on a host), with `-std=c11 -Wall -Wextra -Werror`;
3. run the binary. The exit code is the test result.

Needs a C compiler: `$CC`, else `gcc`.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from ..core.design import Design
from ..core.settings import get_settings
from . import repo_root, run

CFLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-const-variable", "-O1"]
OUT_MAX = 20_000
COMPILE_S = 180
RUN_S = 120


def _clip(s: str) -> str:
    return s if len(s) <= OUT_MAX else s[: OUT_MAX // 2] + "\n...\n" + s[-OUT_MAX // 2 :]


def sources(fw_dir: Path) -> tuple[list[Path], list[Path]]:
    """(C files, include dirs) of the project firmware: everything under `fw/` except `fw/port/`."""
    c: list[Path] = []
    inc: list[Path] = [fw_dir]
    for p in sorted(fw_dir.rglob("*")):
        rel = p.relative_to(fw_dir).parts
        if rel[0] == "port" or p.is_symlink():
            continue
        if p.is_dir():
            inc.append(p)
        elif p.suffix == ".c":
            c.append(p)
    return c, inc


def source_hash(d: Design) -> str:
    """Hash of what the host tests build from in the project: every file under `fw/` except `fw/port/`, and
    `interface.yaml`. A stored host-test result is current only while this hash (and the design hash) holds."""
    root = Path(d.dir).resolve()
    h = hashlib.sha1()
    fw_dir = root / "fw"
    files = [p for p in sorted(fw_dir.rglob("*")) if p.is_file() and not p.is_symlink()
             and p.relative_to(fw_dir).parts[0] != "port"] if fw_dir.is_dir() else []
    iface = root / "interface.yaml"
    for p in files + ([iface] if iface.is_file() else []):
        h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def _check_calls(text: str):
    """(first line, last line, call text) of every `CHECK(...)` call in a C file."""
    for m in re.finditer(r"\bCHECK\s*\(", text):
        depth, i, quote = 0, m.end() - 1, None
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield text.count("\n", 0, m.start()) + 1, text.count("\n", 0, i) + 1, text[m.start():i + 1]


def req_checks(c_files: list[Path], ids: list[str], output: str) -> dict[str, dict]:
    """Host-test checks per requirement: a `CHECK(...)` whose condition or message names a requirement id (as a whole
    word, `R9` or `(R5 + R6)`) counts for it; a `FAIL <file>:<line>:` line of the run marks it failed.
    Returns {id: {"checks": n, "failed": k}} for the ids that some check names."""
    if not ids:
        return {}
    pat = re.compile(r"(?<![\w-])(" + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)) + r")(?![\w-])")
    fails = [(Path(f).name, int(n)) for f, n in re.findall(r"^FAIL (\S+?):(\d+):", output, re.M)]
    out: dict[str, dict] = {}
    for p in c_files:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for first, last, call in _check_calls(text):
            rids = set(pat.findall(call))
            failed = any(f == p.name and first <= n <= last for f, n in fails)
            msg = re.search(r'"((?:[^"\\]|\\.)*)"', call)
            for rid in rids:
                r = out.setdefault(rid, {"checks": 0, "failed": 0, "msgs": []})
                r["checks"] += 1
                r["failed"] += failed
                if msg and len(r["msgs"]) < 4:
                    r["msgs"].append(msg.group(1)[:100])
    return out


def _req_ids(d: Design) -> list[str]:
    import yaml
    rp = Path(d.dir) / "requirements.yaml"
    try:
        reqs = (yaml.safe_load(rp.read_text()) or {}).get("requirements") or [] if rp.is_file() else []
    except (OSError, yaml.YAMLError):
        return []
    return [str(q["id"]) for q in reqs if isinstance(q, dict) and q.get("id")]


def host_test(d: Design, libs, out_dir: Path) -> tuple[int, str, dict]:
    """(exit code, text, summary). 0: tests passed; 1: a contract error, a compile error or a failing test;
    2: nothing to build (no fw/ or no test); 3: no C compiler or no framework."""
    cc = shutil.which(get_settings().tools.cc or "gcc")
    if cc is None:
        return 3, "no C compiler (gcc) on this machine: the firmware host tests cannot run here", {"ran": False}
    fw_dir = Path(d.dir).resolve() / "fw"
    c_files, inc = sources(fw_dir) if fw_dir.is_dir() else ([], [])
    if not any(p.parent.name == "test" for p in c_files):
        return (
            2,
            "no firmware host test: write fw/test/test_app.c (`fw <proj> --scaffold` makes a stub)",
            {"ran": False},
        )
    fwk = repo_root() / "fw" / "framework"
    if not (fwk / "src" / "fwk.c").is_file():
        return 3, f"the firmware framework is missing at {fwk}", {"ran": False}
    path, findings = run(d, libs, out_dir)
    errors = [f for f in findings if f["severity"] == "error"]
    lines = [f"{f['severity']:<8} {f['code']:<24} {f['message']}" for f in findings]
    if errors:
        lines.append(f"{len(errors)} contract errors: the tests were not built")
        return 1, "\n".join(lines), {"ran": False, "contract_errors": len(errors)}
    gen = Path(path).resolve().parent
    exe = gen / "app_tests"
    exe.unlink(missing_ok=True)
    cmd = [
        cc,
        *CFLAGS,
        f"-I{fwk / 'include'}",
        f"-I{fwk / 'test'}",
        f"-I{gen}",
        *(f"-I{i}" for i in inc),
        "-o",
        str(exe),
        str(fwk / "src" / "fwk.c"),
        str(fwk / "test" / "fwk_sim.c"),
        *map(str, c_files),
        "-lm",
    ]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_S, cwd=fw_dir.parent)
    except subprocess.TimeoutExpired:
        return 1, f"compile timed out after {COMPILE_S} s", {"ran": False}
    if cp.returncode != 0:
        msg = (cp.stdout + cp.stderr).replace(str(fw_dir.parent) + "/", "")
        return 1, "\n".join([*lines, "compile failed:", _clip(msg)]), {"ran": False, "compiled": False}
    try:
        rp = subprocess.run([str(exe)], capture_output=True, text=True, timeout=RUN_S, cwd=fw_dir.parent)
    except subprocess.TimeoutExpired:
        return 1, f"the tests did not finish in {RUN_S} s (a hang?)", {"ran": True, "timeout": True}
    out = _clip(rp.stdout + rp.stderr)
    verdict = "PASS" if rp.returncode == 0 else f"FAIL (exit {rp.returncode})"
    text = "\n".join(
        [*lines, f"built {len(c_files)} project files and the framework", out.rstrip(), f"host tests: {verdict}"]
    )
    m = re.findall(r"^(\d+) checks, (\d+) failed", out, re.M)
    summary = {"ran": True, "compiled": True, "rc": rp.returncode, "files": len(c_files),
               "src_hash": source_hash(d), "reqs": req_checks(c_files, _req_ids(d), out)}
    if m:
        summary["checks"], summary["failed"] = int(m[-1][0]), int(m[-1][1])
    return (0 if rp.returncode == 0 else 1), text, summary
