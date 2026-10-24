"""Import graph of src/hwflow: module-level and lazy (function-level) import cycles.

Usage: uv run python scripts/import_graph.py
A module-level edge runs when the module is imported; a lazy edge sits inside a function body and runs on first call.
Cycles are the strongly connected components (size > 1) of the graph, once for module-level edges alone and once for
all edges.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"
PKG = "hwflow"


def modules():
    out = {}
    for p in sorted((ROOT / PKG).rglob("*.py")):
        rel = p.relative_to(ROOT).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        out[".".join(parts)] = p
    return out


def edges(name, path, known):
    """Yield (target, lazy) for each import of a hwflow module in this file."""
    is_pkg = path.name == "__init__.py"
    base = name.split(".") if is_pkg else name.split(".")[:-1]
    tree = ast.parse(path.read_text())

    def visit(node, lazy):
        for ch in ast.iter_child_nodes(node):
            inner = lazy or isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(ch, ast.Import):
                for a in ch.names:
                    if a.name.split(".")[0] == PKG:
                        yield from resolve(a.name, [], inner)
            elif isinstance(ch, ast.ImportFrom):
                if ch.level:
                    mod = base[: len(base) - ch.level + 1] + (ch.module.split(".") if ch.module else [])
                else:
                    mod = ch.module.split(".") if ch.module else []
                if mod and mod[0] == PKG:
                    yield from resolve(".".join(mod), [a.name for a in ch.names], inner)
            yield from visit(ch, inner)

    def resolve(mod, names, lazy):
        for n in names:  # `from hwflow import layout` may name a submodule
            if f"{mod}.{n}" in known:
                yield f"{mod}.{n}", lazy
        if mod in known:
            yield mod, lazy

    yield from visit(tree, False)


def sccs(graph):
    index, low, on, stack, out, i = {}, {}, set(), [], [], [0]

    def strong(v):
        index[v] = low[v] = i[0]; i[0] += 1
        stack.append(v); on.add(v)
        for w in graph[v]:
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    sys.setrecursionlimit(10000)
    for v in graph:
        if v not in index:
            strong(v)
    return out


def two_cycles(graph):
    return sorted({tuple(sorted((a, b))) for a in graph for b in graph[a] if a in graph.get(b, ())})


def main():
    mods = modules()
    mod_edges = {m: set() for m in mods}
    all_edges = {m: set() for m in mods}
    for m, p in mods.items():
        for tgt, lazy in edges(m, p, mods):
            if tgt == m:
                continue
            all_edges[m].add(tgt)
            if not lazy:
                mod_edges[m].add(tgt)
    print(f"{len(mods)} modules, {sum(map(len, mod_edges.values()))} module-level edges, "
          f"{sum(map(len, all_edges.values())) - sum(map(len, mod_edges.values()))} lazy-only edges\n")
    for title, g in (("Module-level cycles (import-time)", mod_edges), ("Cycles including lazy imports", all_edges)):
        print(f"== {title}")
        comps = sccs(g)
        for c in comps:
            print("  group:", ", ".join(x.removeprefix(PKG + ".") for x in c))
        print("  direct two-way pairs:")
        for a, b in two_cycles(g):
            la = "lazy" if b not in mod_edges[a] else "top"
            lb = "lazy" if a not in mod_edges[b] else "top"
            print(f"    {a.removeprefix(PKG + '.')} ({la}) <-> {b.removeprefix(PKG + '.')} ({lb})")
        if not comps:
            print("  none")
        print()


if __name__ == "__main__":
    main()
