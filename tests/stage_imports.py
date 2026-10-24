"""The hwflow modules a stage function can execute: the names its body (and the functions it calls in the stage
modules) uses, mapped to the modules they come from, closed over grimp's import graph (module-level and
function-level imports). Used by tests/test_stage_specs.py."""

from __future__ import annotations

import ast
import importlib
import inspect
from functools import lru_cache

# modules analysed function by function (the stage wrappers and the stage functions); any other module counts whole,
# with everything it imports
FUNCTION_LEVEL = ("hwflow.pipeline.spec", "hwflow.pipeline.stages")


@lru_cache(maxsize=1)
def graph():
    import grimp
    return grimp.build_graph("hwflow")


def _imports(nodes, modname: str) -> dict[str, str]:
    """Local name -> module (or module.attr) bound by the import statements under `nodes`."""
    pkg = modname.rsplit(".", 1)[0]
    out = {}
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.Import):
                for a in n.names:
                    out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    parts = pkg.split(".")[: len(pkg.split(".")) - n.level + 1]
                    base = ".".join(parts + ([n.module] if n.module else []))
                for a in n.names:
                    out[a.asname or a.name] = f"{base}.{a.name}"
    return out


def _resolve(target: str, g) -> str | None:
    """The module of the graph for `pkg.mod.attr` (the longest module prefix)."""
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        m = ".".join(parts[:i])
        if m in g.modules:
            return m
    return None


@lru_cache(maxsize=8)
def _parsed(modname: str):
    tree = ast.parse(inspect.getsource(importlib.import_module(modname)))
    top = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    mod_imports = _imports([n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)], modname)
    return top, mod_imports


def stage_modules(fn, _seen: set | None = None) -> set[str]:
    """Modules the stage function `fn` reaches: its own module, the stage-module functions it calls (followed
    function by function), and every other module whose names they use, with all that module imports."""
    _seen = set() if _seen is None else _seen
    modname = fn.__module__
    top, mod_imports = _parsed(modname)
    g = graph()
    todo, used = [fn.__name__], set()
    while todo:
        name = todo.pop()
        if (modname, name) in _seen or name not in top:
            continue
        _seen.add((modname, name))
        node = top[name]
        local = _imports([node], modname)
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                if n.id in top:
                    todo.append(n.id)
                tgt = local.get(n.id) or mod_imports.get(n.id)
                if tgt:
                    used.add(tgt)
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                tgt = local.get(n.value.id) or mod_imports.get(n.value.id)
                if tgt:
                    used.add(f"{tgt}.{n.attr}")
        used |= set(local.values())
    out = {modname}
    for t in used:
        m = _resolve(t, g)
        if not m or not m.startswith("hwflow"):
            continue
        attr = t[len(m) + 1:].split(".")[0] if len(t) > len(m) else ""
        obj = getattr(importlib.import_module(m), attr, None) if m in FUNCTION_LEVEL and attr else None
        if obj is not None and inspect.isfunction(obj) and obj.__module__ == m:
            out |= stage_modules(obj, _seen)
            continue
        out.add(m)
        out |= {x for x in g.find_upstream_modules(m) if x.startswith("hwflow")}
    return out
