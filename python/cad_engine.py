"""
cad_engine.py — parametric CAD geometry for the SwarmAI 3D-print plugin.

Pure-Python, depends on: numpy, trimesh, manifold3d (booleans), shapely
(optional), matplotlib (optional, previews only). Every solid is built from
CSG primitives and exported as a WATERTIGHT STL via the manifold3d engine.

Two builders are exposed:
  * build_turbine(params)   -> complete Pelton water-turbine generator set
  * build_primitive(params) -> a single parametric printable part
plus validate_stl(path) for watertight/volume/bbox checks.

This module is process-isolated behind cad_runner.py; it prints nothing and
raises on error (the runner serialises results/errors to JSON).
"""

from __future__ import annotations
import os
import math
from typing import Any, Dict, List

import numpy as np
import trimesh
from trimesh.creation import cylinder, box, uv_sphere
from trimesh.transformations import rotation_matrix

ENGINE = "manifold"
SECT = 96


# ───────────────────────── shared helpers ──────────────────────────
def _validate(mesh: trimesh.Trimesh) -> Dict[str, Any]:
    b = mesh.bounds
    size = (b[1] - b[0])
    return {
        "watertight": bool(mesh.is_watertight),
        "volume_cm3": round(float(mesh.volume) / 1000.0, 2),
        "bbox_mm": [round(float(size[0]), 2),
                    round(float(size[1]), 2),
                    round(float(size[2]), 2)],
        "triangles": int(len(mesh.faces)),
    }


def _export(mesh: trimesh.Trimesh, out_dir: str, name: str) -> Dict[str, Any]:
    """Export one STL. manifold3d output is already clean — only repair on a
    real leak (merge_vertices on clean output can CREATE non-manifold edges
    where flat faces meet, e.g. a flange-on-cone seam)."""
    if not mesh.is_watertight:
        mesh.remove_unreferenced_vertices()
        mesh.merge_vertices()
        mesh.fill_holes()
    path = os.path.join(out_dir, name + ".stl")
    mesh.export(path)
    info = _validate(mesh)
    info["name"] = name
    info["path"] = path
    info["bytes"] = os.path.getsize(path)
    return info


def _ellipsoid(a: float, b: float, c: float, count: int = 4) -> trimesh.Trimesh:
    s = uv_sphere(radius=1.0, count=[count * 6, count * 6])
    s.apply_scale([a, b, c])
    return s


def _frustum(r0: float, r1: float, h: float, sections: int = SECT) -> trimesh.Trimesh:
    """A frustum is convex -> convex hull of two coaxial point-rings is
    guaranteed watertight & manifold (robust for booleans)."""
    ang = np.linspace(0, 2 * np.pi, sections, endpoint=False)
    bottom = np.c_[r0 * np.cos(ang), r0 * np.sin(ang), np.full_like(ang, -h / 2)]
    top = np.c_[r1 * np.cos(ang), r1 * np.sin(ang), np.full_like(ang, h / 2)]
    return trimesh.Trimesh(vertices=np.vstack([bottom, top])).convex_hull


def _u(parts):  # union
    return trimesh.boolean.union(parts, engine=ENGINE)


def _d(parts):  # difference (parts[0] - rest)
    return trimesh.boolean.difference(parts, engine=ENGINE)


# ═══════════════════════ PELTON TURBINE SET ════════════════════════
TURBINE_DEFAULTS = dict(
    shaft_d=5.0, bore_clear=0.30, setscrew_d=3.2,
    nema_face=42.3, nema_bolt_pcd=31.0, nema_bolt_d=3.2, nema_pilot_d=22.5,
    pitch_radius=45.0, n_buckets=18, jet_d=8.0,
    hub_d=30.0, hub_len=24.0, disc_t=4.0,
    wall=3.5, side_clear=6.0, radial_clear=9.0, outlet_w=34.0,
    inlet_d=16.0, nozzle_len=42.0, nozzle_wall=3.0,
)


def _make_bucket(JET: float) -> trimesh.Trimesh:
    L = 2.8 * JET            # radial length
    W = 3.4 * JET            # axial width (two cups side by side in Z)
    D = 1.5 * JET            # tangential depth
    blank = box(extents=[L, D, W]); blank.apply_translation([L / 2.0, 0, 0])
    ax, ay, az = 0.95 * (L / 2.0), 0.95 * (D / 2.0), 0.80 * (W / 4.0)
    cup_off_z = W / 4.0
    cavities = []
    for sgn in (+1, -1):
        cav = _ellipsoid(ax, ay, az, count=4)
        cav.apply_translation([L / 2.0, 0.55 * D, sgn * cup_off_z])
        cavities.append(cav)
    bucket = _d([blank, _u(cavities)])
    notch = box(extents=[L * 0.5, D * 1.2, W * 0.42])
    notch.apply_translation([L * 0.92, 0.35 * D, 0])
    bucket = _d([bucket, notch])
    stem = box(extents=[10.0, D * 0.9, W * 0.7]); stem.apply_translation([-3.0, 0, 0])
    return _u([bucket, stem])


def _make_runner(P: Dict[str, float]) -> trimesh.Trimesh:
    RP = P["pitch_radius"]; JET = P["jet_d"]; BORE = P["shaft_d"] + P["bore_clear"]
    parts: List[trimesh.Trimesh] = [
        cylinder(radius=RP + 2.0, height=P["disc_t"], sections=SECT),
        cylinder(radius=P["hub_d"] / 2.0, height=P["hub_len"], sections=SECT),
    ]
    bucket0 = _make_bucket(JET)
    n = int(P["n_buckets"])
    for i in range(n):
        th = i * 2.0 * math.pi / n
        b = bucket0.copy()
        b.apply_translation([RP - 14.0, 0, 0])
        b.apply_transform(rotation_matrix(th, [0, 0, 1]))
        parts.append(b)
    runner = _u(parts)
    bore = cylinder(radius=BORE / 2.0, height=P["hub_len"] + 4, sections=SECT)
    runner = _d([runner, bore])
    boss = cylinder(radius=5.5, height=8.0, sections=48)
    boss.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
    boss.apply_translation([0, -(P["hub_d"] / 2.0 + 2.0), P["hub_len"] / 2 - 3])
    runner = _u([runner, boss])
    ss = cylinder(radius=P["setscrew_d"] / 2.0, height=P["hub_d"] + 20, sections=32)
    ss.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
    ss.apply_translation([0, 0, P["hub_len"] / 2 - 3])
    runner = _d([runner, ss])
    runner.apply_translation([0, 0, P["hub_len"] / 2.0])
    return runner


def _make_nozzle(P: Dict[str, float]) -> trimesh.Trimesh:
    L = P["nozzle_len"]; r_in = P["inlet_d"] / 2.0; r_out = P["jet_d"] / 2.0; w = P["nozzle_wall"]
    outer = _frustum(r_in + w, r_out + w + 1.0, L)
    inner = _frustum(r_in, r_out, L + 2)
    nozzle = _d([outer, inner])
    collar = cylinder(radius=r_in + w + 3.0, height=10.0, sections=SECT)
    collar.apply_translation([0, 0, -L / 2.0 - 1.0])
    cb = cylinder(radius=r_in, height=14.0, sections=SECT); cb.apply_translation([0, 0, -L / 2.0 - 1.0])
    nozzle = _u([nozzle, _d([collar, cb])])
    flange = box(extents=[2 * (r_out + w + 8), 8.0, 2 * (r_out + w + 8)])
    flange.apply_translation([0, 0, L / 2.0 - 1.0])
    fb = cylinder(radius=r_out + 0.5, height=10, sections=SECT); fb.apply_translation([0, 0, L / 2.0 - 1.0])
    return _u([nozzle, _d([flange, fb])])


def _nema_holes(P, depth, z):
    holes = []
    h = P["nema_bolt_pcd"] / 2.0
    for sx in (+1, -1):
        for sy in (+1, -1):
            c = cylinder(radius=P["nema_bolt_d"] / 2.0, height=depth, sections=32)
            c.apply_translation([sx * h, sy * h, z]); holes.append(c)
    pilot = cylinder(radius=P["nema_pilot_d"] / 2.0, height=depth, sections=SECT)
    pilot.apply_translation([0, 0, z]); holes.append(pilot)
    return holes


def _casing_inner_r(P):
    return P["pitch_radius"] + 2.8 * P["jet_d"] * 0.55 + P["radial_clear"]


def _make_housing(P):
    JET = P["jet_d"]; RP = P["pitch_radius"]; BORE = P["shaft_d"] + P["bore_clear"]
    Ri = _casing_inner_r(P); Ro = Ri + P["wall"]
    inner_w = (3.4 * JET) + 2 * P["side_clear"]; back_t = P["wall"]
    outer = cylinder(radius=Ro, height=inner_w + back_t, sections=SECT)
    cav = cylinder(radius=Ri, height=inner_w + 0.1, sections=SECT)
    cav.apply_translation([0, 0, back_t / 2.0 + 0.05])
    housing = _d([outer, cav])
    z_back = -(inner_w + back_t) / 2.0
    sh = cylinder(radius=BORE / 2.0 + 1.5, height=back_t + 4, sections=SECT)
    sh.apply_translation([0, 0, z_back + back_t / 2.0]); housing = _d([housing, sh])
    boss = box(extents=[P["nema_face"] + 8, P["nema_face"] + 8, 6.0])
    boss.apply_translation([0, 0, z_back - 3.0]); housing = _u([housing, boss])
    for c in _nema_holes(P, 20.0, z_back - 3.0):
        housing = _d([housing, c])
    outlet = box(extents=[P["outlet_w"], 4 * P["wall"], inner_w + back_t + 4])
    outlet.apply_translation([0, -Ro, 0]); housing = _d([housing, outlet])
    port = cylinder(radius=JET / 2.0 + 4.0, height=2 * P["wall"] + 6, sections=48)
    port.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
    port.apply_translation([RP, Ro - P["wall"], 0]); housing = _u([housing, port])
    pb = cylinder(radius=JET / 2.0 + 0.6, height=4 * P["wall"] + 12, sections=48)
    pb.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
    pb.apply_translation([RP, Ro, 0]); housing = _d([housing, pb])
    for sx in (+1, -1):
        foot = box(extents=[16, 10, 8]); foot.apply_translation([sx * (Ro - 8), -Ro + 6, 0])
        housing = _u([housing, foot])
    meta = dict(Ri=Ri, Ro=Ro, inner_w=inner_w, back_t=back_t, z_back=z_back)
    return housing, meta


def _make_cover(P, m):
    Ri, Ro, wall = m["Ri"], m["Ro"], P["wall"]
    cover = cylinder(radius=Ro, height=wall, sections=SECT)
    lip = cylinder(radius=Ri - 0.3, height=4.0, sections=SECT)
    lip.apply_translation([0, 0, -(wall / 2 + 2.0)]); cover = _u([cover, lip])
    vent = cylinder(radius=4.0, height=wall + 8, sections=48); cover = _d([cover, vent])
    outlet = box(extents=[P["outlet_w"], 2 * wall + 8, 4 * wall])
    outlet.apply_translation([0, -Ro, 0]); return _d([cover, outlet])


def _make_base(P, m):
    Ro = m["Ro"]
    plate = box(extents=[2 * Ro + 30, 70, 8]); walls = []
    for sx in (+1, -1):
        w = box(extents=[10, 70, 40]); w.apply_translation([sx * (Ro - 4), 0, 24]); walls.append(w)
    base = _u([plate] + walls)
    for sx in (+1, -1):
        c = cylinder(radius=2.0, height=16, sections=24)
        c.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
        c.apply_translation([sx * (Ro - 4), 0, 36]); base = _d([base, c])
    return base


def _make_coupling(P):
    BORE = P["shaft_d"] + P["bore_clear"]; L = 24.0
    body = cylinder(radius=9.0, height=L, sections=SECT)
    b1 = cylinder(radius=BORE / 2.0, height=L / 2 + 2, sections=SECT); b1.apply_translation([0, 0, L / 4 + 1])
    b2 = cylinder(radius=BORE / 2.0, height=L / 2 + 2, sections=SECT); b2.apply_translation([0, 0, -(L / 4 + 1)])
    coup = _d([body, b1, b2])
    for zc in (+L / 4, -L / 4):
        ss = cylinder(radius=P["setscrew_d"] / 2.0, height=24, sections=24)
        ss.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0])); ss.apply_translation([0, 0, zc])
        coup = _d([coup, ss])
    return coup


_TURBINE_PARTS = {
    "runner": lambda P, m: _make_runner(P),
    "nozzle": lambda P, m: _make_nozzle(P),
    "cover": lambda P, m: _make_cover(P, m),
    "base": lambda P, m: _make_base(P, m),
    "coupling": lambda P, m: _make_coupling(P),
    # housing is built first (provides shared geometry meta) — handled inline
}


def build_turbine(params: Dict[str, Any]) -> Dict[str, Any]:
    P = dict(TURBINE_DEFAULTS)
    for k, v in (params.get("dimensions") or {}).items():
        if k in P and v is not None:
            P[k] = float(v)
    out_dir = params["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    wanted = params.get("parts") or (["housing"] + list(_TURBINE_PARTS.keys()))
    reports: List[Dict[str, Any]] = []
    housing, hmeta = _make_housing(P)
    if "housing" in wanted:
        reports.append(_export(housing, out_dir, "housing"))
    for name in wanted:
        if name == "housing":
            continue
        if name not in _TURBINE_PARTS:
            continue
        mesh = _TURBINE_PARTS[name](P, hmeta)
        reports.append(_export(mesh, out_dir, name))
    preview = _render_preview(out_dir, reports) if params.get("preview", True) else None
    return {"kind": "turbine", "out_dir": out_dir, "parts": reports,
            "preview": preview, "params": P}


# ═══════════════════════ PRIMITIVE PARTS ════════════════════════════
def build_primitive(params: Dict[str, Any]) -> Dict[str, Any]:
    shape = params["shape"]; d = params.get("dimensions") or {}
    out_dir = params["out_dir"]; os.makedirs(out_dir, exist_ok=True)
    name = params.get("name") or shape

    def g(key, default):
        v = d.get(key)
        return float(v) if v is not None else float(default)

    if shape == "box":
        mesh = box(extents=[g("x", 20), g("y", 20), g("z", 20)])
    elif shape == "cylinder":
        mesh = cylinder(radius=g("d", 20) / 2.0, height=g("h", 20), sections=SECT)
    elif shape == "tube":
        outer = cylinder(radius=g("od", 20) / 2.0, height=g("h", 20), sections=SECT)
        inner = cylinder(radius=g("id", 12) / 2.0, height=g("h", 20) + 2, sections=SECT)
        mesh = _d([outer, inner])
    elif shape == "cone":
        mesh = _frustum(g("d0", 20) / 2.0, g("d1", 2) / 2.0, g("h", 30))
    elif shape == "sphere":
        mesh = _ellipsoid(g("d", 20) / 2.0, g("d", 20) / 2.0, g("d", 20) / 2.0, count=6)
    elif shape == "washer":  # flat ring
        outer = cylinder(radius=g("od", 20) / 2.0, height=g("t", 3), sections=SECT)
        inner = cylinder(radius=g("id", 10) / 2.0, height=g("t", 3) + 2, sections=SECT)
        mesh = _d([outer, inner])
    elif shape == "standoff":  # hex/round standoff with bore
        body = cylinder(radius=g("d", 10) / 2.0, height=g("h", 20), sections=int(g("sides", SECT)))
        bore = cylinder(radius=g("bore", 3.2) / 2.0, height=g("h", 20) + 2, sections=48)
        mesh = _d([body, bore])
    else:
        raise ValueError(f"unknown shape '{shape}'. Supported: box, cylinder, tube, "
                         f"cone, sphere, washer, standoff")
    info = _export(mesh, out_dir, name)
    return {"kind": "primitive", "out_dir": out_dir, "parts": [info], "preview": None}


# ═══════════════════════ GENERAL DESIGN (ask-anything) ══════════════
# Two backends the model can pick per request:
#   * "mesh"  — trimesh + manifold3d CSG via the `cadlib` vocabulary.
#               Always available; robust, watertight-first.
#   * "brep"  — build123d (OpenCascade B-rep): fillets, chamfers, lofts,
#               sweeps, sketches, STEP export. Requires `pip install
#               build123d`; degrades to a clear error when absent.
# Generated code sets `result = <solid>` (single part) or
# `parts = {"name": <solid>, ...}` (assembly). Each solid is validated and,
# on the mesh backend, repaired (manifold re-union + hole fill) before export.

_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in (
        "range", "len", "abs", "min", "max", "round", "enumerate", "zip",
        "list", "dict", "tuple", "set", "float", "int", "str", "bool",
        "sum", "sorted", "map", "filter", "pow", "divmod", "print",
        "reversed", "isinstance", "ValueError", "Exception",
    )
}


def _collect_outputs(ns: Dict[str, Any]) -> Dict[str, Any]:
    """Pull {name: solid} out of the executed namespace. Accepts a `parts`
    dict (assembly) or a single `result`. Raises with a helpful message
    when neither is set (the #1 weak-model mistake)."""
    if isinstance(ns.get("parts"), dict) and ns["parts"]:
        return dict(ns["parts"])
    if ns.get("result") is not None:
        return {"part": ns["result"]}
    raise ValueError(
        "code produced no geometry: set `result = <solid>` for a single part, "
        "or `parts = {\"name\": <solid>, ...}` for an assembly."
    )


def _repair_mesh(mesh: "trimesh.Trimesh") -> "trimesh.Trimesh":
    """Best-effort heal of a non-watertight mesh: a single-operand manifold
    union re-meshes most CSG seams, then fill remaining holes."""
    if mesh.is_watertight:
        return mesh
    try:
        healed = trimesh.boolean.union([mesh], engine=ENGINE)
        if healed is not None and healed.is_watertight:
            return healed
        if healed is not None:
            mesh = healed
    except Exception:
        pass
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()
    mesh.fill_holes()
    return mesh


def _design_mesh(code: str, out_dir: str, name: str, preview: bool) -> Dict[str, Any]:
    import cadlib
    ns: Dict[str, Any] = {"__builtins__": _SAFE_BUILTINS, "np": np, "math": math, "cadlib": cadlib}
    for fn in cadlib.__all__:
        ns[fn] = getattr(cadlib, fn)
    exec(compile(code, "<cad_design>", "exec"), ns)
    outputs = _collect_outputs(ns)
    reports: List[Dict[str, Any]] = []
    for part_name, mesh in outputs.items():
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError(
                f"'{part_name}' is {type(mesh).__name__}, not a solid. On the mesh "
                f"backend every part must be a cadlib/trimesh solid."
            )
        mesh = _repair_mesh(mesh)
        safe = part_name if part_name == name or len(outputs) > 1 else name
        reports.append(_export(mesh, out_dir, safe))
    preview_path = _render_preview(out_dir, reports) if preview else None
    return {"kind": "design", "backend": "mesh", "out_dir": out_dir,
            "parts": reports, "preview": preview_path}


def _design_brep(code: str, out_dir: str, name: str, preview: bool, export_step: bool) -> Dict[str, Any]:
    try:
        import build123d as _b3d  # noqa: F401
    except Exception as e:
        return {
            "ok": False,
            "error": "brep backend requires build123d (OpenCascade). "
                     "Install with:  pip install build123d",
            "missing_deps": ["build123d"],
            "detail": str(e),
        }
    from build123d import export_stl  # type: ignore
    try:
        from build123d import export_step  # type: ignore
    except Exception:
        export_step = None  # older build123d
    ns: Dict[str, Any] = {"math": math}
    exec("from build123d import *", ns)        # full build123d vocabulary
    exec(compile(code, "<cad_design>", "exec"), ns)
    outputs = _collect_outputs(ns)
    reports: List[Dict[str, Any]] = []
    for part_name, shape in outputs.items():
        # build123d BuildPart context → its .part; algebra API → the shape.
        solid = getattr(shape, "part", shape)
        safe = part_name if (part_name == name or len(outputs) > 1) else name
        stl_path = os.path.join(out_dir, safe + ".stl")
        export_stl(solid, stl_path)
        if export_step:
            try:
                export_step(solid, os.path.join(out_dir, safe + ".step"))
            except Exception:
                pass
        m = trimesh.load(stl_path, force="mesh")
        info = _validate(m)
        info["name"] = safe
        info["path"] = stl_path
        info["bytes"] = os.path.getsize(stl_path)
        reports.append(info)
    preview_path = _render_preview(out_dir, reports) if preview else None
    return {"kind": "design", "backend": "brep", "out_dir": out_dir,
            "parts": reports, "preview": preview_path}


def build_design(params: Dict[str, Any]) -> Dict[str, Any]:
    code = params.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("`code` (Python geometry source) is required.")
    out_dir = params["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    name = params.get("name") or "part"
    preview = params.get("preview", True)
    backend = (params.get("backend") or "mesh").lower()
    if backend in ("mesh", "trimesh", "a"):
        return _design_mesh(code, out_dir, name, preview)
    if backend in ("brep", "build123d", "b"):
        return _design_brep(code, out_dir, name, preview, bool(params.get("export_step", False)))
    raise ValueError(f"unknown backend '{backend}'. Use 'mesh' or 'brep'.")


# ═══════════════════════ VALIDATE / PREVIEW ═════════════════════════
def validate_stl(path: str) -> Dict[str, Any]:
    mesh = trimesh.load(path, force="mesh")
    info = _validate(mesh)
    info["path"] = path
    info["winding_consistent"] = bool(mesh.is_winding_consistent)
    info["euler_number"] = int(mesh.euler_number)
    info["printable"] = bool(mesh.is_watertight and mesh.is_winding_consistent)
    return info


def _render_preview(out_dir: str, reports: List[Dict[str, Any]]):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        n = len(reports)
        cols = min(3, n); rows = (n + cols - 1) // cols
        fig = plt.figure(figsize=(5 * cols, 4 * rows))
        palette = ["#d98c5f", "#6fae6f", "#7aa2c8", "#b08fd0", "#c9a23f", "#cf6f6f"]
        for i, r in enumerate(reports, 1):
            m = trimesh.load(r["path"], force="mesh")
            ax = fig.add_subplot(rows, cols, i, projection="3d")
            tris = m.vertices[m.faces]
            ax.add_collection3d(Poly3DCollection(
                tris, alpha=1.0, facecolor=palette[(i - 1) % len(palette)],
                edgecolor="#33333322", linewidths=0.1))
            v = m.vertices; c = (v.max(0) + v.min(0)) / 2; rad = (v.max(0) - v.min(0)).max() / 2
            ax.set_xlim(c[0]-rad, c[0]+rad); ax.set_ylim(c[1]-rad, c[1]+rad); ax.set_zlim(c[2]-rad, c[2]+rad)
            ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=25, azim=35)
            ax.set_title(f"{r['name']}.stl"); ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
        plt.tight_layout()
        path = os.path.join(out_dir, "preview.png")
        plt.savefig(path, dpi=100); plt.close()
        return path
    except Exception:
        return None
