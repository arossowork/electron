"""Minimal S-expression reader/writer for KiCad and Specctra files.

Atoms are kept as Python str. Quoted strings are wrapped in `Q` so that the
writer can quote them again. Lists are plain Python lists.
"""

from __future__ import annotations

import re


class Q(str):
    """A quoted string atom."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def loads(text: str):
    pos = 0
    stack: list[list] = [[]]
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"sexpr parse error at {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            lst = stack.pop()
            stack[-1].append(lst)
        elif m.group(3) is not None:
            stack[-1].append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        elif m.group(4):
            stack[-1].append(m.group(4))
    if len(stack) != 1:
        raise ValueError("unbalanced parentheses")
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def _atom(a) -> str:
    if isinstance(a, Q):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, float):
        s = f"{a:.6f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(a)


def dumps(node, indent: int = 0) -> str:
    if not isinstance(node, list):
        return _atom(node)
    # short lists of atoms stay on one line
    if all(not isinstance(x, list) for x in node) or (
        len(node) <= 6 and all(not isinstance(x, list) or all(not isinstance(y, list) for y in x) for x in node)
        and sum(len(_atom(x)) if not isinstance(x, list) else 20 for x in node) < 100
    ):
        return "(" + " ".join(dumps(x) for x in node) + ")"
    pad = "  " * (indent + 1)
    head = []
    rest = []
    for x in node:
        (rest if (isinstance(x, list) or rest) else head).append(x)
    out = "(" + " ".join(_atom(x) for x in head)
    for x in rest:
        out += "\n" + pad + dumps(x, indent + 1)
    return out + "\n" + "  " * indent + ")"


def find(node, key):
    """First child list whose head is `key`."""
    for x in node:
        if isinstance(x, list) and x and x[0] == key:
            return x
    return None


def find_all(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def value(node, key, default=None):
    f = find(node, key)
    return f[1] if f and len(f) > 1 else default
