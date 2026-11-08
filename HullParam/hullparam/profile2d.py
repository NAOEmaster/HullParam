"""2-D profile utilities for planar members (frames, girders, plates).

A planar face lying in a global plane (x=const, y=const or z=const) is mapped to
2-D coordinates, processed with shapely (robust offsets / booleans) and turned
back into an exact OCC face made of straight lines and B-splines whose end
points coincide exactly (no gaps).

OpenCASCADE's own BRepOffsetAPI_MakeOffset crashes on the GPPH B-spline hull
sections, which is why offsets are done in 2-D here.
"""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.GeomAbs import GeomAbs_C2
from OCP.ShapeFix import ShapeFix_Face
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

from . import occ
from .occ import GeometryError

# axis -> (index of the two in-plane coordinates)
PLANE_IDX = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}


def to3d(axis: str, value, uv: np.ndarray) -> np.ndarray:
    """2-D (u,v) -> 3-D on the plane  axis = value + a*u + b*v.
    ``value`` is a number (a=b=0) or a tuple (value, a, b) for slightly
    tilted planes (e.g. COMSOL frames with a small tilt angle)."""
    v0, a, b = (value, 0.0, 0.0) if np.isscalar(value) else value
    i, j = PLANE_IDX[axis]
    k = occ.AXES[axis]
    p = np.zeros((len(uv), 3))
    p[:, i] = uv[:, 0]
    p[:, j] = uv[:, 1]
    p[:, k] = v0 + a * uv[:, 0] + b * uv[:, 1]
    return p


def gp_plane(axis: str, value) -> gp_Pln:
    v0, a, b = (value, 0.0, 0.0) if np.isscalar(value) else value
    i, j = PLANE_IDX[axis]
    k = occ.AXES[axis]
    n = np.zeros(3)
    n[k] = 1.0
    n[i] = -a
    n[j] = -b
    n /= np.linalg.norm(n)
    o = np.zeros(3)
    o[k] = v0
    return gp_Pln(gp_Pnt(*map(float, o)), gp_Dir(*map(float, n)))


def wire_ring(wire, axis: str, deflection: float = 0.05) -> np.ndarray:
    """Ordered 2-D points of a closed wire."""
    i, j = PLANE_IDX[axis]
    pts = []
    ex = BRepTools_WireExplorer(wire)
    while ex.More():
        e = ex.Current()
        p = occ.edge_discretize(e, deflection)
        if pts:
            p = p[1:]
        pts.extend(p.tolist())
        ex.Next()
    a = np.array(pts)[:, [i, j]]
    return a


def face_to_polygon(face, axis: str, deflection: float = 0.05) -> Polygon:
    """Exact-boundary polygon (outer + holes) of a planar face."""
    from OCP.BRepTools import BRepTools
    outer = BRepTools.OuterWire_s(face)
    rings = []
    for w in occ.unique(occ.explore(face, "wire")):
        r = wire_ring(w, axis, deflection)
        if len(r) >= 3:
            rings.append((w.IsSame(outer), r))
    shell = [r for o, r in rings if o]
    holes = [r for o, r in rings if not o]
    if not shell:
        raise GeometryError("면의 외곽 경계를 찾지 못했습니다.")
    poly = Polygon(shell[0], holes).buffer(0)
    return poly


def shape_to_polygon(shape, axis: str, deflection: float = 0.05):
    polys = [face_to_polygon(f, axis, deflection) for f in occ.faces(shape)]
    return unary_union(polys) if polys else Polygon()


def polygons(geom) -> List[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


# ----------------------------------------------------------------------
# polygon -> exact OCC face
# ----------------------------------------------------------------------
def _clean_ring(ring: np.ndarray, tol: float) -> np.ndarray:
    r = np.asarray(ring, float)
    if np.allclose(r[0], r[-1]):
        r = r[:-1]
    keep = [0]
    for k in range(1, len(r)):
        if np.linalg.norm(r[k] - r[keep[-1]]) > tol:
            keep.append(k)
    r = r[keep]
    if len(r) > 3 and np.linalg.norm(r[0] - r[-1]) <= tol:
        r = r[:-1]
    return r


def _corners(r: np.ndarray, corner_deg: float) -> List[int]:
    n = len(r)
    idx = []
    for k in range(n):
        a = r[k] - r[k - 1]
        b = r[(k + 1) % n] - r[k]
        la, lb = np.linalg.norm(a), np.linalg.norm(b)
        if la == 0 or lb == 0:
            continue
        c = np.clip(a @ b / (la * lb), -1, 1)
        if math.degrees(math.acos(c)) > corner_deg:
            idx.append(k)
    return idx


def _runs(r: np.ndarray, corner_deg: float, max_pts: int = 400):
    """Split a closed ring into open runs between corner vertices."""
    n = len(r)
    cs = _corners(r, corner_deg)
    if not cs:
        # smooth closed curve: split into 4 runs so every edge is open
        cs = [0, n // 4, n // 2, 3 * n // 4]
    runs = []
    for a, b in zip(cs, cs[1:] + [cs[0] + n]):
        seg = [r[k % n] for k in range(a, b + 1)]
        # keep fitted curves manageable
        while len(seg) > max_pts:
            mid = len(seg) // 2
            runs.append(np.array(seg[:mid + 1]))
            seg = seg[mid:]
        runs.append(np.array(seg))
    return runs


def _fit_bspline(p3: np.ndarray, tol: float):
    arr = TColgp_Array1OfPnt(1, len(p3))
    for k, p in enumerate(p3, 1):
        arr.SetValue(k, gp_Pnt(*map(float, p)))
    fit = GeomAPI_PointsToBSpline(arr, 3, 8, GeomAbs_C2, tol)
    if not fit.IsDone():
        return None
    crv = fit.Curve()
    crv.SetPole(1, gp_Pnt(*map(float, p3[0])))
    crv.SetPole(crv.NbPoles(), gp_Pnt(*map(float, p3[-1])))
    return crv


def _deviation(crv, p3: np.ndarray) -> float:
    """Max distance curve<->points, both directions (Hausdorff-like)."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    n = max(4 * len(p3), 50)
    t0, t1 = crv.FirstParameter(), crv.LastParameter()
    cs = np.array([[crv.Value(t).X(), crv.Value(t).Y(), crv.Value(t).Z()]
                   for t in np.linspace(t0, t1, n)])
    d1 = max(_dist3(c, p3) for c in cs)          # curve -> polyline
    d2 = 0.0                                       # points -> curve
    for q in p3:
        pr = GeomAPI_ProjectPointOnCurve(gp_Pnt(*map(float, q)), crv)
        if pr.NbPoints() == 0:
            return float("inf")
        d2 = max(d2, pr.LowerDistance())
    return max(d1, d2)


def _dist3(c, p3):
    """Distance from point c to polyline p3 (3-D)."""
    a, b = p3[:-1], p3[1:]
    ab = b - a
    L2 = np.einsum("ij,ij->i", ab, ab)
    L2[L2 == 0] = 1e-12
    t = np.clip(np.einsum("ij,ij->i", c - a, ab) / L2, 0, 1)
    proj = a + ab * t[:, None]
    return float(np.min(np.linalg.norm(proj - c, axis=1)))


def _edges_from_run(p3: np.ndarray, tol: float, depth: int = 0) -> list:
    """Straight segment if collinear within tol, else a C2 B-spline whose end
    points are exactly the run end points.  Every spline is verified against
    the points; if it deviates more than 2*tol the run is split and refitted,
    down to straight segments.  Guarantees fidelity and no gaps."""
    a, b = p3[0], p3[-1]
    if len(p3) == 2:
        return [occ.segment(a, b)]
    ab = b - a
    L = np.linalg.norm(ab)
    if L > 0:
        d = np.linalg.norm(np.cross(p3 - a, ab), axis=1) / L
        if d.max() <= tol:
            return [occ.segment(a, b)]
    if len(p3) >= 4 and depth < 12:
        crv = _fit_bspline(p3, tol)
        if crv is not None and _deviation(crv, p3) <= 2 * tol:
            return [BRepBuilderAPI_MakeEdge(crv).Edge()]
    if len(p3) <= 3 or depth >= 12:
        return [occ.segment(p3[k], p3[k + 1]) for k in range(len(p3) - 1)]
    # split at the vertex with the largest turning angle (or the middle)
    v1 = p3[1:-1] - p3[:-2]
    v2 = p3[2:] - p3[1:-1]
    cosang = np.einsum("ij,ij->i", v1, v2) / (
        np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1) + 1e-12)
    k = int(np.argmin(cosang)) + 1
    if cosang[k - 1] > 0.9999:
        k = len(p3) // 2
    return (_edges_from_run(p3[:k + 1], tol, depth + 1) +
            _edges_from_run(p3[k:], tol, depth + 1))


def _ring_wire(ring2d, axis, value, tol, corner_deg):
    r = _clean_ring(ring2d, tol)
    if len(r) < 3:
        raise GeometryError("다각형 점이 부족합니다.")
    mw = BRepBuilderAPI_MakeWire()
    for run in _runs(r, corner_deg):
        for e in _edges_from_run(to3d(axis, value, run), tol):
            mw.Add(e)
            if not mw.IsDone():
                raise GeometryError("경계 와이어 생성 실패")
    w = mw.Wire()
    if not w.Closed():
        raise GeometryError("경계 와이어가 닫히지 않았습니다.")
    return w


def polygon_to_face(poly: Polygon, axis: str, value: float,
                    tol: float = 0.05, corner_deg: float = 12.0):
    """Planar OCC face (with holes) from a shapely polygon."""
    poly = poly.simplify(tol / 2, preserve_topology=True)
    pln = gp_plane(axis, value)
    outer = _ring_wire(np.asarray(poly.exterior.coords), axis, value, tol,
                       corner_deg)
    mf = BRepBuilderAPI_MakeFace(pln, outer, True)
    for h in poly.interiors:
        mf.Add(_ring_wire(np.asarray(h.coords), axis, value, tol, corner_deg))
    if not mf.IsDone():
        raise GeometryError("평면 면 생성 실패")
    fx = ShapeFix_Face(mf.Face())
    fx.FixOrientation()
    fx.Perform()
    return occ.as_face(fx.Face())


# ----------------------------------------------------------------------
# frame opening
# ----------------------------------------------------------------------
ROUNDING_NOTES: list = []   # (requested r, used r) - read & cleared by build


def frame_opening(section: Polygon, web: float, deck_beam: float,
                  z_deck: float, corner_radius: float = 0.0):
    """Opening (hole) of a web frame: section offset inwards by ``web``,
    cut below the deck by ``deck_beam``.  Optional rounded opening corners."""
    if web <= 0:
        raise GeometryError("늑골 웨브 깊이(web_depth)는 0보다 커야 합니다.")
    # merge pieces that touch along a line (e.g. port / starboard halves of a
    # tilted station) - otherwise the inward offset would also eat the joint
    section = section.buffer(0.05, join_style=2, mitre_limit=10.0).buffer(
        -0.05, join_style=2, mitre_limit=10.0)
    if len(polygons(section)) > 1:
        section = max(polygons(section), key=lambda p: p.area)
    minx, miny, maxx, maxy = section.bounds
    # extend the section vertically above the deck so that the inward offset
    # acts on sides / bottom only; the deck side is cut by ``deck_beam``.
    band = section.intersection(Polygon([(minx - 1, z_deck - 1.0),
                                         (maxx + 1, z_deck - 1.0),
                                         (maxx + 1, maxy + 1),
                                         (minx - 1, maxy + 1)]))
    if not band.is_empty:
        y0, _, y1, _ = band.bounds
        ext = Polygon([(y0, z_deck - 1.0), (y1, z_deck - 1.0),
                       (y1, z_deck + 4 * web + deck_beam),
                       (y0, z_deck + 4 * web + deck_beam)])
        section = unary_union([section, ext]).buffer(0)
    inner = section.buffer(-web, join_style=2, mitre_limit=10.0)
    if inner.is_empty:
        return inner
    top = z_deck - deck_beam
    cutbox = Polygon([(minx - 10, miny - 10), (maxx + 10, miny - 10),
                      (maxx + 10, top), (minx - 10, top)])
    op = inner.intersection(cutbox)
    if corner_radius > 0 and not op.is_empty:
        r = float(corner_radius)
        from shapely.ops import polylabel
        parts = []
        for pg in polygons(op):
            # rounding must not erase the opening: limit r to 90 % of the
            # largest inscribed circle radius of this opening
            rin = pg.exterior.distance(polylabel(pg, tolerance=1.0))
            rr = min(r, 0.9 * rin)
            if rr < r - 1e-6:
                ROUNDING_NOTES.append((round(r, 1), round(rr, 1)))
            parts.append(pg.buffer(-rr, join_style=1, quad_segs=32).buffer(
                rr, join_style=1, quad_segs=32) if rr > 0.5 else pg)
        op = unary_union(parts)
    # drop slivers
    ps = [p for p in polygons(op) if p.area > 100.0]
    return unary_union(ps) if ps else Polygon()


def measure_frame(section: Polygon, material) -> dict:
    """Estimate web depth / deck beam depth of an existing frame
    (material = union polygon of the frame faces at that station)."""
    opening = section.difference(material.buffer(0.5)).buffer(0)
    ops = polygons(opening)
    if not ops:
        return {}
    op = max(ops, key=lambda p: p.area)
    if op.area < 0.02 * section.area:
        return {}
    z_top_open = op.bounds[3]
    z_deck = section.bounds[3]
    deck_beam = z_deck - z_top_open
    ring = np.asarray(op.exterior.coords)
    keep = ring[ring[:, 1] < z_top_open - 1.0]
    if len(keep) < 5:
        keep = ring
    # densify
    ls = LineString(op.exterior.coords)
    d = [section.exterior.distance(ls.interpolate(t, normalized=True))
         for t in np.linspace(0, 1, 400)]
    pts = [ls.interpolate(t, normalized=True) for t in np.linspace(0, 1, 400)]
    d = np.array([dd for dd, p in zip(d, pts) if p.y < z_top_open - 1.0])
    web = float(np.median(d)) if len(d) else float("nan")
    return {"web_depth": round(web, 1), "deck_beam_depth": round(deck_beam, 1),
            "opening_area": op.area}


def iou(a, b) -> float:
    if a.is_empty and b.is_empty:
        return 1.0
    u = a.union(b).area
    return a.intersection(b).area / u if u else 0.0
