"""Placement geometry shared by the placer, the board writer and the blocks: KiCad rotation, the part record and the
rigid unit (a layout block). Pure data and arithmetic; no I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass


def rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """KiCad rotation (counter-clockwise on screen, y axis down)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


@dataclass
class Part:
    ref: str
    w: float          # courtyard size at rot 0 (with margin)
    h: float
    cx: float         # courtyard center offset from footprint origin at rot 0
    cy: float
    pads: dict        # pad number -> (x, y) at rot 0
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    fixed: bool = False
    edge: str | None = None
    group: str | None = None
    decouples: str | None = None
    pad_size: dict | None = None  # pad number -> (w, h) at rot 0, for the copper-to-edge check
    sides: dict | None = None     # "front"/"back" -> (w, h, cx, cy) courtyard per board side
    tht: frozenset = frozenset()  # numbers of the plated through-hole pads (KiCad: not inside another courtyard)
    npth: tuple = ()  # (x, y, w, h) at rot 0 of the non-plated holes (mounting pegs, no number): through the board too
    nocu: frozenset = frozenset()  # numbers of the pads without copper (a keep-out marker's dummy pad)
    cu: dict | None = None        # pad number -> its copper layers (frozenset, "*" = every layer); None: every layer
    round: frozenset = frozenset()  # numbers of the circular pads (the corridor term measures them as circles)

    def side_box(self, side: str):
        if not self.sides or side not in self.sides:
            return None
        w, h, cx, cy = self.sides[side]
        ox, oy = rot(cx, cy, self.r)
        if self.r % 180:
            w, h = h, w
        x, y = self.x + ox, self.y + oy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def box(self):
        cx, cy = rot(self.cx, self.cy, self.r)
        w, h = (self.w, self.h) if self.r % 180 == 0 else (self.h, self.w)
        x, y = self.x + cx, self.y + cy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def npth_boxes(self) -> list:
        """Board boxes of the non-plated holes (KiCad npth_inside_courtyard: no other courtyard over them, either side)."""
        out = []
        for hx, hy, w, h in self.npth:
            dx, dy = rot(hx, hy, self.r)
            if self.r % 180:
                w, h = h, w
            out.append((self.x + dx - w / 2, self.y + dy - h / 2, self.x + dx + w / 2, self.y + dy + h / 2))
        return out

    def pad(self, n):
        px, py = self.pads[n]
        dx, dy = rot(px, py, self.r)
        return self.x + dx, self.y + dy


class Parts(dict):
    """ref -> Part, with the rule areas of the design's keep-out footprints (`areas`, see keepout.placement_areas)
    and the layout blocks that move as one unit (`blocks`, see blocks.units)."""
    areas: list = ()
    blocks: list = ()
    libs = None


@dataclass
class Rigid:
    """Parts that move as one unit (a layout block): `members` are (ref, dx, dy, drot) from the unit origin, `yard` is
    the virtual part that holds the unit courtyard (ref `self.ref`, not a component). x, y, r place the origin."""
    name: str
    ref: str
    members: list
    yard: Part
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    fixed: bool = False

    def refs(self) -> list[str]:
        return [m[0] for m in self.members] + [self.ref]

    def sync(self, parts):
        """Move the members and the courtyard to the unit origin."""
        for ref, dx, dy, dr in self.members:
            ox, oy = rot(dx, dy, self.r)
            p = parts[ref]
            p.x, p.y, p.r = self.x + ox, self.y + oy, (dr + self.r) % 360
        self.yard.x, self.yard.y, self.yard.r = self.x, self.y, self.r

    def pull(self, parts):
        """The unit origin from its first member (a saved placement), then every member from the origin."""
        ref, dx, dy, dr = self.members[0]
        p = parts[ref]
        self.r = (p.r - dr) % 360
        ox, oy = rot(dx, dy, self.r)
        self.x, self.y = p.x - ox, p.y - oy
        self.sync(parts)


COURTYARD_MARGIN = 0.25  # mm around each courtyard in the placer's boxes


def cu_layers(n: int) -> list[str]:
    """Copper layer names of an n-layer board, front to back."""
    return ["F.Cu"] + (["In1.Cu", "In2.Cu"] if n == 4 else []) + ["B.Cu"]
