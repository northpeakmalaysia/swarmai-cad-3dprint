"""
cadlib.py — curated, watertight-friendly geometry vocabulary for the
`cad_design` MESH backend (Backend A: trimesh + manifold3d).

This is the API the LLM writes against when it wants to generate an
arbitrary printable part ("ask anything"). It deliberately exposes a
small, documented, CSG-first surface so generated code is reliable and
the output stays watertight — instead of the model improvising against
raw trimesh internals.

Conventions
-----------
- All units are millimetres.
- Every primitive is centred at the origin unless noted. Use
  `translate(...)` / `place_on_bed(...)` to position parts.
- Every function returns a `trimesh.Trimesh`. Boolean ops route through
  the `manifold3d` engine, which keeps results watertight.
- For fillets, chamfers, lofts, sweeps and STEP export, use the BREP
  backend (Backend B: build123d) instead — those are not robust on a
  pure mesh kernel.

The `cad_design` runner injects every name in `__all__` directly into the
exec namespace, plus `np` and `math`, so generated code can call e.g.
`union(box(20,20,5), cylinder(10, 8))` with no imports.
"""

from __future__ import annotations
import math
from typing import List, Sequence, Tuple, Union as _U

import numpy as np
import trimesh
from trimesh.creation import box as _tbox, cylinder as _tcyl, uv_sphere as _tsphere
from trimesh.transformations import rotation_matrix

ENGINE = "manifold"
SECT = 96

Solid = trimesh.Trimesh
Point2D = Tuple[float, float]

__all__ = [
    # primitives
    "box", "cube", "cylinder", "tube", "cone", "frustum", "sphere",
    "ellipsoid", "prism", "washer",
    # 2D -> 3D
    "extrude", "rounded_polygon", "regular_polygon", "hull", "loft", "revolve",
    # booleans
    "union", "difference", "intersect",
    # transforms
    "translate", "rotate", "scale", "mirror", "place_on_bed", "center",
    # patterns
    "linear_pattern", "radial_pattern",
    # introspection
    "is_watertight", "bbox", "SECT",
]


def _axis_vec(axis: _U[str, Sequence[float]]) -> List[float]:
    if isinstance(axis, str):
        return {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[axis.lower()]
    return list(axis)


# ───────────────────────── primitives ──────────────────────────
def box(x: float, y: float, z: float) -> Solid:
    """Axis-aligned box, centred at origin. Dimensions in mm."""
    return _tbox(extents=[float(x), float(y), float(z)])


def cube(s: float) -> Solid:
    return box(s, s, s)


def cylinder(d: float, h: float, sections: int = SECT) -> Solid:
    """Cylinder of diameter `d`, height `h`, centred at origin (z in ±h/2)."""
    return _tcyl(radius=float(d) / 2.0, height=float(h), sections=sections)


def frustum(d0: float, d1: float, h: float, sections: int = SECT) -> Solid:
    """Truncated cone, diameters `d0` (bottom) → `d1` (top), height `h`.
    Built as a convex hull of two coaxial rings → always watertight."""
    r0, r1 = float(d0) / 2.0, float(d1) / 2.0
    ang = np.linspace(0, 2 * np.pi, sections, endpoint=False)
    bottom = np.c_[r0 * np.cos(ang), r0 * np.sin(ang), np.full_like(ang, -h / 2.0)]
    top = np.c_[r1 * np.cos(ang), r1 * np.sin(ang), np.full_like(ang, h / 2.0)]
    return trimesh.Trimesh(vertices=np.vstack([bottom, top])).convex_hull


def cone(d: float, h: float, sections: int = SECT) -> Solid:
    """Full cone (apex at top): diameter `d` base → point. height `h`."""
    return frustum(d, 0.001, h, sections)


def tube(od: float, id: float, h: float, sections: int = SECT) -> Solid:
    """Hollow cylinder: outer dia `od`, inner dia `id`, height `h`."""
    return difference(cylinder(od, h, sections), cylinder(id, h + 2.0, sections))


def ellipsoid(dx: float, dy: float, dz: float, count: int = 6) -> Solid:
    s = _tsphere(radius=1.0, count=[count * 6, count * 6])
    s.apply_scale([float(dx) / 2.0, float(dy) / 2.0, float(dz) / 2.0])
    return s


def sphere(d: float, count: int = 6) -> Solid:
    return ellipsoid(d, d, d, count)


def prism(sides: int, d: float, h: float) -> Solid:
    """Regular-polygon prism (e.g. sides=6 → hex). `d` = circumscribed dia."""
    return _tcyl(radius=float(d) / 2.0, height=float(h), sections=int(sides))


def washer(od: float, id: float, t: float, sections: int = SECT) -> Solid:
    return tube(od, id, t, sections)


# ───────────────────────── 2D → 3D ──────────────────────────
def extrude(points: Sequence[Point2D], h: float) -> Solid:
    """Extrude a closed 2D polygon (list of (x, y) in mm) by height `h`.
    The polygon sits on z=0 and rises to z=h. Watertight by construction."""
    try:
        from shapely.geometry import Polygon
    except Exception as e:  # pragma: no cover - shapely is a declared dep
        raise RuntimeError(f"extrude needs shapely: {e}")
    poly = Polygon([(float(x), float(y)) for x, y in points])
    if not poly.is_valid:
        poly = poly.buffer(0)  # heal self-intersections
    return trimesh.creation.extrude_polygon(poly, height=float(h))


def rounded_polygon(points: Sequence[Point2D], r: float) -> List[Point2D]:
    """Return a new polygon (list of (x,y)) with corners rounded by radius
    `r` — useful as input to `extrude`. Uses the shapely buffer trick."""
    from shapely.geometry import Polygon
    poly = Polygon([(float(x), float(y)) for x, y in points])
    rounded = poly.buffer(float(r), join_style=1).buffer(-float(r), join_style=1)
    return list(rounded.exterior.coords)


def regular_polygon(sides: int, d: float) -> List[Point2D]:
    """Vertices of a regular polygon (circumscribed diameter `d`) for use
    with `extrude`."""
    r = float(d) / 2.0
    return [(r * math.cos(2 * math.pi * i / sides), r * math.sin(2 * math.pi * i / sides))
            for i in range(int(sides))]


def hull(*items) -> Solid:
    """Convex hull of meshes and/or 3D points. Always watertight. Great for
    a quick loft between cross-sections or smoothing a blocky union."""
    pts: List[np.ndarray] = []
    for it in items:
        if isinstance(it, trimesh.Trimesh):
            pts.append(np.asarray(it.vertices))
        else:
            pts.append(np.atleast_2d(np.asarray(it, dtype=float)))
    return trimesh.Trimesh(vertices=np.vstack(pts)).convex_hull


def loft(bottom: Sequence[Point2D], top: Sequence[Point2D], h: float) -> Solid:
    """Convex loft between a bottom polygon (z=0) and top polygon (z=h).
    Result is the convex hull of both rings → watertight, but only convex
    shapes are faithful. For true (concave) lofts use the brep backend."""
    b = np.c_[np.asarray(bottom, dtype=float), np.zeros(len(bottom))]
    t = np.c_[np.asarray(top, dtype=float), np.full(len(top), float(h))]
    return trimesh.Trimesh(vertices=np.vstack([b, t])).convex_hull


def revolve(profile: Sequence[Point2D], sections: int = SECT) -> Solid:
    """Revolve a closed 2D profile around the Z axis. `profile` is a list of
    (radius, z) points with radius ≥ 0. Returns a watertight solid of
    revolution. For partial-angle revolves use the brep backend."""
    ls = [(float(r), float(z)) for r, z in profile]
    return trimesh.creation.revolve(ls, sections=sections)


# ───────────────────────── booleans ──────────────────────────
def union(*solids: Solid) -> Solid:
    parts = [s for s in solids if s is not None]
    return trimesh.boolean.union(parts, engine=ENGINE) if len(parts) > 1 else parts[0]


def difference(a: Solid, *rest: Solid) -> Solid:
    return trimesh.boolean.difference([a, *rest], engine=ENGINE)


def intersect(*solids: Solid) -> Solid:
    return trimesh.boolean.intersection(list(solids), engine=ENGINE)


# ───────────────────────── transforms ──────────────────────────
def translate(s: Solid, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Solid:
    c = s.copy()
    c.apply_translation([float(x), float(y), float(z)])
    return c


def rotate(s: Solid, deg: float, axis: _U[str, Sequence[float]] = "z",
           point: Sequence[float] = (0, 0, 0)) -> Solid:
    c = s.copy()
    c.apply_transform(rotation_matrix(math.radians(float(deg)), _axis_vec(axis), list(point)))
    return c


def scale(s: Solid, fx: float, fy: float = None, fz: float = None) -> Solid:
    c = s.copy()
    if fy is None and fz is None:
        c.apply_scale(float(fx))
    else:
        c.apply_scale([float(fx), float(fy if fy is not None else fx),
                       float(fz if fz is not None else fx)])
    return c


def mirror(s: Solid, axis: str = "x") -> Solid:
    """Mirror across the plane normal to `axis` (through origin). Winding is
    repaired so the result stays a valid solid."""
    idx = {"x": 0, "y": 1, "z": 2}[axis.lower()]
    factors = [1.0, 1.0, 1.0]
    factors[idx] = -1.0
    c = s.copy()
    c.apply_scale(factors)
    c.fix_normals()
    return c


def place_on_bed(s: Solid) -> Solid:
    """Translate so the lowest point sits on z=0 (print bed)."""
    return translate(s, z=-float(s.bounds[0][2]))


def center(s: Solid) -> Solid:
    """Translate so the bounding-box centre is at the origin."""
    c = (s.bounds[0] + s.bounds[1]) / 2.0
    return translate(s, -c[0], -c[1], -c[2])


# ───────────────────────── patterns ──────────────────────────
def linear_pattern(s: Solid, n: int, dx: float = 0, dy: float = 0, dz: float = 0) -> Solid:
    return union(*[translate(s, dx * i, dy * i, dz * i) for i in range(int(n))])


def radial_pattern(s: Solid, n: int, axis: _U[str, Sequence[float]] = "z") -> Solid:
    return union(*[rotate(s, 360.0 * i / int(n), axis) for i in range(int(n))])


# ───────────────────────── introspection ──────────────────────────
def is_watertight(s: Solid) -> bool:
    return bool(s.is_watertight)


def bbox(s: Solid) -> List[float]:
    size = s.bounds[1] - s.bounds[0]
    return [round(float(size[0]), 2), round(float(size[1]), 2), round(float(size[2]), 2)]
