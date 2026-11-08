"""Generate the shell + beam structure from parameters.

Pipeline
    envelope (fixed hull skin)  +  Layout (expanded parameters)
      -> member faces (bulkheads, web frames, girders, plates)
      -> beam edges (stringers, flanges, stiffeners, centre-line beams)
      -> General Fuse (BOPAlgo_Builder): every intersection is imprinted so
         shells share edges with each other and beams lie on shell edges
      -> named members (shell / beam) with thickness / section properties

Exactness measures (avoid sliver faces / tiny gaps):
  * a station closer than ``build.snap_tolerance`` to an existing transverse
    skin seam is placed exactly on that seam's (possibly tilted) planes;
  * girders / stringers / plates are snapped to existing seams the same way;
  * member ends that reference a station (BH1, F3, ...) are cut exactly on
    that station's surface.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry import box as sbox

from OCP.BOPAlgo import BOPAlgo_Builder

from . import occ
from . import params as P
from . import profile2d as p2
from .envelope import Envelope
from .occ import GeometryError

OPENING_TOL = 0.5     # mm, fit tolerance of frame opening curves
NEAR_SEAM = 50.0      # mm, warn when a member is this close to an old skin seam
BIG = 1.0e4


@dataclass
class Member:
    name: str
    kind: str                 # 'shell' | 'beam'
    category: str             # hull / bulkhead / frame / girder / plate / stringer ...
    props: Dict = field(default_factory=dict)
    shapes: List = field(default_factory=list)   # faces (shell) or edges (beam)

    @property
    def size(self) -> float:
        if self.kind == "shell":
            return sum(occ.area(s) for s in self.shapes)
        return sum(occ.length(s) for s in self.shapes)


@dataclass
class Model:
    members: List[Member]
    shape: object                 # fused compound (all faces + edges)
    envelope: Envelope
    layout: P.Layout
    params: dict
    log: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)

    def by_kind(self, kind):
        return [m for m in self.members if m.kind == kind]


# ----------------------------------------------------------------------
class StationGeo:
    """Exact surface of a station: one x-plane, or two half planes
    (port y>=0 / starboard y<0) when snapped to a tilted existing seam."""

    def __init__(self, st: P.Station, env: Envelope, snap_tol: float):
        self.st = st
        sp = env.station_plane(st.x, snap_tol)
        if sp is not None:
            port, stbd = tuple(sp["port"]), tuple(sp["stbd"])
            if max(abs(port[1]), abs(port[2]), abs(stbd[1]), abs(stbd[2])) < 1e-12 \
                    and abs(port[0] - stbd[0]) < 1e-9:
                self.parts = [("all", (port[0], 0.0, 0.0))]
            else:
                self.parts = [("port", port), ("stbd", stbd)]
            self.x = float(sp["x"])
            self.snapped = abs(self.x - st.x)
        else:
            self.parts = [("all", (st.x, 0.0, 0.0))]
            self.x = st.x
            self.snapped = None

    def x_at(self, y: float, z: float) -> float:
        for side, (x0, a, b) in self.parts:
            if side == "all" or (side == "port" and y >= 0) or (side == "stbd" and y < 0):
                return x0 + a * y + b * z
        return self.x

    @staticmethod
    def half(side) -> Polygon:
        if side == "port":
            return sbox(0.0, -BIG, BIG, BIG)
        if side == "stbd":
            return sbox(-BIG, -BIG, 0.0, BIG)
        return sbox(-BIG, -BIG, BIG, BIG)


def _faces_crossing(faces, axis, value, pad=1.0):
    k = occ.AXES[axis]
    out = []
    for f in faces:
        bb = occ.bbox(f, optimal=False)
        if bb[k] - pad <= value <= bb[k + 3] + pad:
            out.append(f)
    return out


def _edges_in_plane(faces, axis, value, tol=1e-3):
    k = occ.AXES[axis]
    out = []
    for f in faces:
        for e in occ.edges(f):
            pts = occ.edge_points(e, 7)
            if np.all(np.abs(pts[:, k] - value) < tol) and occ.length(e) > 1e-3:
                out.append(e)
    return occ.unique(out)


def _section_edges(host_faces, axis, value):
    """Intersection curves of a plane with host faces.  Existing boundary
    edges lying exactly in the plane are reused (no degenerate sections)."""
    fs = _faces_crossing(host_faces, axis, value)
    if not fs:
        return []
    k = occ.AXES[axis]
    on = _edges_in_plane(fs, axis, value)
    if on:
        # faces having one of these edges on their boundary are already covered
        touching = []
        for f in fs:
            fe = occ.edges(f)
            if any(e.IsSame(o) for e in fe for o in on):
                touching.append(f)
        rest = [f for f in fs if not any(f.IsSame(t) for t in touching)]
        # a face touching the plane on its boundary may still be crossed inside
        rest += [f for f in touching
                 if occ.bbox(f, optimal=True)[k] < value - 0.01 and
                 occ.bbox(f, optimal=True)[k + 3] > value + 0.01]
    else:
        rest = fs
    out = list(on)
    if rest:
        res = occ.section(occ.compound(rest), occ.plane_face(axis, value))
        new = [e for e in occ.edges(res) if occ.length(e) > 1e-3]
        # drop section pieces that duplicate an existing boundary edge
        for e in new:
            c = occ.centroid(e, 1)
            if not any(np.linalg.norm(occ.centroid(o, 1) - c) < 1e-3 and
                       abs(occ.length(o) - occ.length(e)) < 1e-3 for o in on):
                out.append(e)
    return occ.unique(out)


def _clip(edges, xmin=-1e9, xmax=1e9, ymin=-1e9, ymax=1e9, zmin=-1e9, zmax=1e9,
          snap: float = 0.0):
    """Keep the parts of edges inside a box.  With ``snap`` > 0 an x limit
    closer than ``snap`` to an existing vertex is moved onto that vertex
    (no tiny edge pieces next to seam ends)."""
    if not edges:
        return []
    if snap > 0:
        vx = [occ.edge_points(e, 2)[:, 0] for e in edges]
        vx = np.concatenate(vx) if vx else np.array([])
        lo, hi = vx.min(), vx.max()
        for lim_name in ("xmin", "xmax"):
            v = xmin if lim_name == "xmin" else xmax
            if abs(v) >= 1e8:
                continue
            k = np.argmin(np.abs(vx - v))
            if abs(vx[k] - v) <= snap:
                v = float(vx[k])
                if (lim_name == "xmin" and v <= lo + 1e-9) or (lim_name == "xmax" and v >= hi - 1e-9):
                    v = -1e9 if lim_name == "xmin" else 1e9   # limit at the very end: no clip
            if lim_name == "xmin":
                xmin = v
            else:
                xmax = v
    big = 1e7
    lim = [max(xmin, -big), max(ymin, -big), max(zmin, -big),
           min(xmax, big), min(ymax, big), min(zmax, big)]
    if lim == [-big, -big, -big, big, big, big]:
        return edges
    res = occ.common(occ.compound(edges), occ.box(*lim))
    return [e for e in occ.edges(res) if occ.length(e) > 1e-3]


def _near_point(edges, x):
    """(y, z) of the edge point closest to the transverse position x."""
    best, bd = None, 1e18
    for e in edges:
        pts = occ.edge_points(e, 25)
        i = int(np.argmin(np.abs(pts[:, 0] - x)))
        d = abs(pts[i, 0] - x)
        if d < bd:
            bd, best = d, pts[i]
    return (0.0, 0.0) if best is None else (float(best[1]), float(best[2]))


def _seam_edges(env: Envelope, cls: str, axis: str, value: float, tol=0.3):
    """Existing skin edges on plane axis=value between two faces of class
    ``cls`` (exact re-use of seams, e.g. the keel / deck centre line)."""
    k = occ.AXES[axis]
    fmap = occ.edge_face_map(env.solid)
    cl = {}
    for f, c in zip(env.skin_faces, env.classes):
        cl.setdefault(f.__hash__(), []).append((f, c))

    def cls_of(face):
        for g, c in cl.get(face.__hash__(), []):
            if g.IsSame(face):
                return c
        return None
    out = []
    for i in range(1, fmap.Extent() + 1):
        e = occ.as_edge(fmap.FindKey(i))
        fs = occ.list_of_shape(fmap.FindFromIndex(i))
        if len(fs) != 2 or any(cls_of(occ.as_face(f)) != cls for f in fs):
            continue
        pts = occ.edge_points(e, 7)
        if np.all(np.abs(pts[:, k] - value) < tol):
            out.append(e)
    return out


# ----------------------------------------------------------------------
def build(params: dict, project_dir: str, log=print, progress=None) -> Model:
    t_all = time.time()
    tm: Dict[str, float] = {}

    def step(msg, frac):
        log(msg)
        if progress:
            progress(frac, msg)

    step("[1/6] 외판(envelope) 불러오기", 0.02)
    env = Envelope.load(project_dir)
    hull = env.info
    step("[2/6] 파라미터 검증 및 전개", 0.05)
    lay = P.expand(params, hull)
    for w in lay.warnings:
        log("  [주의] " + w)
    members: List[Member] = []
    warnings = list(lay.warnings)
    bp = params["build"]
    fuzzy = float(bp.get("fuzzy_tolerance", 0.01))
    snap = float(bp.get("snap_tolerance", 2.0))
    snaps: List[str] = []
    near: Dict[str, float] = {}     # member -> distance to an old seam (2..50 mm)

    from .envelope import seam_coordinates
    seam_cache: Dict[str, Dict[str, list]] = {}

    def snap_host(axis, v, tol, classes):
        """Nearest seam coordinate on the host skin faces (within tol)."""
        key = "+".join(classes)
        if key not in seam_cache:
            seam_cache[key] = seam_coordinates(env.faces_of(*classes))
        vals = seam_cache[key].get(axis, [])
        if not vals:
            return v
        a = np.asarray(vals)
        k = int(np.argmin(np.abs(a - v)))
        return float(a[k]) if abs(a[k] - v) <= tol else v

    def snap_val(axis, v, what, classes):
        s = snap_host(axis, v, snap, classes)
        if s != v:
            snaps.append(f"{what}: {axis}={v:.3f} → 기존 외판 분할선 {s:.3f} 에 맞춤")
        else:
            n = snap_host(axis, v, NEAR_SEAM, classes)
            if n != v:
                key = what[:-2] if what.endswith(("_P", "_S")) else what
                near.setdefault(key, abs(n - v))
        return s

    # ---------------- hull skin members
    for cls in ("deck", "side", "bottom", "chine", "transom"):
        fs = env.faces_of(cls)
        if fs:
            members.append(Member(f"Hull_{cls.capitalize()}", "shell", "hull",
                                  {"thickness": lay.plating[cls], "skin_class": cls}, fs))

    # ---------------- stations
    t0 = time.time()
    step(f"[3/6] 횡부재 생성: 격벽 {sum(s.kind == 'bulkhead' for s in lay.stations)}개, "
         f"늑골 {sum(s.kind == 'frame' for s in lay.stations)}개", 0.08)
    geo: Dict[str, StationGeo] = {}
    station_faces: Dict[str, list] = {}
    nst = max(1, len(lay.stations))
    for i, st in enumerate(lay.stations):
        if progress:
            progress(0.08 + 0.3 * i / nst, f"  {st.name} x={st.x:.1f}")
        g = StationGeo(st, env, snap)
        geo[st.name] = g
        if g.snapped is not None and g.snapped > 1e-6:
            snaps.append(f"{st.name}: x={st.x:.3f} → 기존 외판 분할선 {g.x:.3f} 에 맞춤")
        elif g.snapped is None:
            sp_near = env.station_plane(st.x, NEAR_SEAM)
            if sp_near is not None:
                near[st.name] = abs(sp_near["x"] - st.x)
        parts = []           # (side, plane, section faces)
        for side, pl in g.parts:
            f = p2.polygon_to_face(StationGeo.half(side), "x", pl)
            parts.append((side, pl, occ.faces(occ.common(f, env.solid))))
        sec_faces = [f for _, _, fs in parts for f in fs]
        if not sec_faces:
            raise GeometryError(f"{st.name} (x={st.x:.1f}) 위치에서 선체 단면이 없습니다.")
        props = {"thickness": st.thickness, "x": round(g.x, 3)}
        if st.kind == "bulkhead":
            members.append(Member(st.name, "shell", "bulkhead", props, sec_faces))
            station_faces[st.name] = sec_faces
            continue
        S = p2.shape_to_polygon(occ.compound(sec_faces), "x")
        p2.ROUNDING_NOTES.clear()
        O = p2.frame_opening(S, st.web_depth, st.deck_beam_depth,
                             hull["z_deck"], st.corner_radius)
        if p2.ROUNDING_NOTES:
            warnings.append(f"{st.name} (x={st.x:.0f}): 개구부가 좁아 모서리 라운드 반경을 "
                            f"{p2.ROUNDING_NOTES[0][0]} → {min(n[1] for n in p2.ROUNDING_NOTES)} mm 로 줄임")
        props.update({"web_depth": round(st.web_depth, 2),
                      "deck_beam_depth": round(st.deck_beam_depth, 2),
                      "corner_radius": round(st.corner_radius, 2)})
        if not [p for p in p2.polygons(O) if p.area > 100.0]:
            warnings.append(f"{st.name} (x={st.x:.0f}): 웨브 깊이가 단면에 비해 커서 "
                            "개구부가 없습니다 → 막힌 판으로 생성")
            members.append(Member(st.name, "shell", "frame", props, sec_faces))
            station_faces[st.name] = sec_faces
            continue
        fr_faces, fl_edges = [], []
        for side, pl, fs in parts:
            ops = [p for p in p2.polygons(O.intersection(StationGeo.half(side)))
                   if p.area > 100.0]
            if not ops:
                fr_faces += fs
                continue
            op_faces = [p2.polygon_to_face(p, "x", pl, tol=OPENING_TOL) for p in ops]
            cut = occ.faces(occ.cut(occ.compound(fs), occ.compound(op_faces)))
            fr_faces += cut
            for of in op_faces:
                for e in occ.edges(of):
                    pts = occ.edge_points(e, 5)
                    if side != "all" and np.all(np.abs(pts[:, 1]) < 1e-6):
                        continue     # centre-line split, not a free edge
                    fl_edges.append(e)
        if not fr_faces:
            raise GeometryError(f"{st.name}: 늑골 면 생성 실패 (개구부 차감 결과 없음)")
        members.append(Member(st.name, "shell", "frame", props, fr_faces))
        station_faces[st.name] = fr_faces
        if st.flange and fl_edges:
            members.append(Member(f"{st.name}_Flange", "beam", "frame_flange",
                                  {"section": st.flange, "host": st.name}, fl_edges))
    tm["stations"] = time.time() - t0

    def ref_x(ref, fallback, y, z):
        if ref is None:
            return fallback
        name, off = ref
        g = geo.get(name)
        return fallback if g is None else g.x_at(y, z) + off

    # ---------------- girders / plates
    t0 = time.time()
    step(f"[4/6] 종부재 생성: 거더 {len(lay.girders)}개, 수평판 {len(lay.plates)}개", 0.4)
    zlow = hull["z_keel"] - 10.0
    girder_y = []
    for gd in lay.girders:
        y = snap_val("y", gd.y, gd.name, ("bottom", "chine"))
        girder_y.append(abs(y))
        faces, tops = [], []
        for (x0, x1, zt, r0, r1) in gd.segments:
            zt0, zt1 = P.interp(zt, x0), P.interp(zt, x1)
            a_lo, a_hi = ref_x(r0, x0, y, zlow), ref_x(r0, x0, y, zt0)
            b_lo, b_hi = ref_x(r1, x1, y, zlow), ref_x(r1, x1, y, zt1)
            inner = [] if isinstance(zt, (int, float)) else \
                [(x, P.interp(zt, x)) for x in zt["x"] if max(a_hi, a_lo) < x < min(b_hi, b_lo)]
            top = [(a_hi, P.interp(zt, a_hi))] + inner + [(b_hi, P.interp(zt, b_hi))]
            poly = Polygon([(a_lo, zlow), (b_lo, zlow)] + top[::-1])
            if not poly.is_valid or poly.area <= 0:
                raise GeometryError(f"{gd.name}: 구간 형상이 올바르지 않습니다 (x 범위/상단 높이 확인).")
            f = p2.polygon_to_face(poly, "y", y)
            faces += occ.faces(occ.common(f, env.solid))
            tops.append((min(a_lo, a_hi), max(b_lo, b_hi), zt))
        if not faces:
            warnings.append(f"{gd.name} (y={gd.y:.0f}): 선체 안에 걸리는 부분이 없어 생략")
            continue
        members.append(Member(gd.name, "shell", "girder",
                              {"thickness": gd.thickness, "y": round(y, 3)}, faces))
        if gd.flange:
            def top_z(x, segs=tops):
                vals = [P.interp(zt, x) for (a, b, zt) in segs if a - 1 <= x <= b + 1]
                return min(vals) if vals else np.nan
            top = []
            for f in faces:
                for e in occ.edges(f):
                    pts = occ.edge_points(e, 7)
                    zt_at = np.array([top_z(x) for x in pts[:, 0]])
                    if np.all(np.abs(pts[:, 2] - zt_at) < 0.5):
                        top.append(e)
            top = occ.unique(top)
            if top:
                members.append(Member(f"{gd.name}_Flange", "beam", "girder_flange",
                                      {"section": gd.flange, "host": gd.name}, top))
            else:
                warnings.append(f"{gd.name}: 상단 플랜지 선을 찾지 못했습니다.")
    for pl in lay.plates:
        y0, y1 = pl.y0, pl.y1
        for gy in girder_y:           # plate edges sitting on girders -> exact
            for s in (gy, -gy):
                if abs(y0 - s) <= snap:
                    y0 = s
                if abs(y1 - s) <= snap:
                    y1 = s
        ys = [y0] + ([0.0] if y0 < 0 < y1 else []) + [y1]
        aft = [(ref_x(pl.x0_ref, pl.x0, y, pl.z), y) for y in ys]
        fwd = [(ref_x(pl.x1_ref, pl.x1, y, pl.z), y) for y in ys]
        poly = Polygon(aft[::-1] + fwd)       # (x, y) in plane z
        if not poly.is_valid or poly.area <= 0:
            raise GeometryError(f"{pl.name}: 판 형상이 올바르지 않습니다.")
        f = p2.polygon_to_face(poly, "z", pl.z)
        faces = occ.faces(occ.common(f, env.solid))
        if faces:
            members.append(Member(pl.name, "shell", "plate",
                                  {"thickness": pl.thickness, "z": pl.z}, faces))
        else:
            warnings.append(f"{pl.name}: 선체 안에 걸리는 부분이 없어 생략")
    fixed = os.path.join(project_dir, "fixed_members.brep")
    if os.path.isfile(fixed):
        ff = occ.faces(occ.read_brep(fixed))
        if ff:
            members.append(Member("Fixed_Members", "shell", "fixed",
                                  {"thickness": float(params["fixed_members"]["thickness"])}, ff))
    tm["longitudinal"] = time.time() - t0

    # ---------------- beams
    t0 = time.time()
    step(f"[5/6] 보강재(beam) 생성: {len(lay.beams)}개 + 중심선", 0.5)
    host_cache = {c: env.faces_of(c) for c in ("deck", "side", "bottom", "chine", "transom")}
    nb = max(1, len(lay.beams))
    for i, b in enumerate(lay.beams):
        if progress and i % 4 == 0:
            progress(0.5 + 0.2 * i / nb, f"  {b.name}")
        if b.host == "bulkhead":
            hosts = station_faces.get(b.station, [])
            value = b.value
        else:
            hosts = host_cache.get(b.host, [])
            value = snap_val(b.axis, b.value, b.name, (b.host,))
        es = _section_edges(hosts, b.axis, value)
        if not es:
            warnings.append(f"{b.name}: 해당 위치에 붙을 판이 없어 생략 ({b.axis}={b.value:.1f})")
            continue
        if b.axis == "z":
            out = []
            for sgn in (1, -1):
                side = [e for e in es if sgn * occ.centroid(e, 1)[1] >= 0]
                if not side:
                    continue
                y_a, _ = _near_point(side, b.x0)
                y_b, _ = _near_point(side, b.x1)
                xa = ref_x(b.x0_ref, b.x0, y_a, value)
                xb = ref_x(b.x1_ref, b.x1, y_b, value)
                out += _clip(side, xa, xb, *((0.0, b.y_abs_max) if sgn > 0 else (-b.y_abs_max, 0.0)),
                             snap=snap)
            es = out
        else:
            _, z_a = _near_point(es, b.x0)
            _, z_b = _near_point(es, b.x1)
            xa = ref_x(b.x0_ref, b.x0, value, z_a)
            xb = ref_x(b.x1_ref, b.x1, value, z_b)
            es = _clip(es, xa, xb, zmin=b.z0, zmax=b.z1, snap=snap)
        if not es:
            warnings.append(f"{b.name}: 지정 범위 안에 붙을 판이 없어 생략")
            continue
        props = {"section": b.section, "host": b.station or b.host, b.axis: round(value, 3)}
        members.append(Member(b.name, "beam", b.group, props, es))
    for k, cls in (("keel", "bottom"), ("deck", "deck")):
        sec = lay.centerline.get(k)
        if not sec:
            continue
        es = _seam_edges(env, cls, "y", 0.0)
        if not es:
            es = _section_edges(host_cache[cls], "y", 0.0)
        if es:
            members.append(Member(f"Centerline_{k.capitalize()}", "beam", "centerline",
                                  {"section": sec, "host": cls}, es))
        else:
            warnings.append(f"중심선 {k} 보강재 선을 찾지 못했습니다.")
    tm["beams"] = time.time() - t0
    if near:
        items = sorted(near.items(), key=lambda kv: kv[1])
        txt = ", ".join(f"{k}({d:.0f} mm)" for k, d in items[:12]) + (" …" if len(items) > 12 else "")
        warnings.append(f"기존 외판 분할선과 {snap:g}~{NEAR_SEAM:g} mm 떨어진 부재 {len(items)}개: {txt} "
                        "→ 그 사이에 좁은 띠 면이 생깁니다(형상은 정상). 의도한 위치가 아니면 "
                        "build.snap_tolerance 를 그 거리보다 크게 하면 분할선에 맞춰집니다.")
    if snaps:
        log(f"  기존 외판 분할선에 맞춘 위치 {len(snaps)}건 (snap_tolerance={snap} mm)")

    # ---------------- general fuse
    t0 = time.time()
    n_args = sum(len(m.shapes) for m in members)
    step(f"[6/6] 전체 교차·연결(General Fuse): 인자 {n_args}개", 0.72)
    fused, members = fuse(members, fuzzy)
    tm["fuse"] = time.time() - t0
    rp = dict(FUSE_REPAIR)
    if rp.get("faces_repaired"):
        log(f"  교차 후 면 {rp['faces_repaired']}개의 모서리 매개변수 곡선을 수리 "
            f"(최대 모서리 공차 {rp['max_edge_tolerance']} mm)")
        if rp["max_edge_tolerance"] > 0.1:
            warnings.append(f"교차 후 면 {rp['faces_repaired']}개 모서리 수리, 최대 공차 "
                            f"{rp['max_edge_tolerance']} mm (메싱에는 문제 없음, 정밀도 참고)")
    tm["total"] = time.time() - t_all
    model = Model(members, fused, env, lay, params, warnings=warnings, timings=tm)
    model.log = snaps
    log(f"생성 완료: 쉘 부재 {len(model.by_kind('shell'))}개, 빔 부재 "
        f"{len(model.by_kind('beam'))}개, {tm['total']:.1f} s")
    if progress:
        progress(1.0, "완료")
    return model


def fuse(members: List[Member], fuzzy: float):
    """General Fuse of all member shapes; returns (result, members with images)."""
    b = BOPAlgo_Builder()
    for m in members:
        for s in m.shapes:
            b.AddArgument(s)
    if fuzzy > 0:
        b.SetFuzzyValue(fuzzy)
    b.SetRunParallel(True)
    b.SetUseOBB(True)
    b.Perform()
    if b.HasErrors():
        raise GeometryError("General Fuse(전체 교차 연결)에 실패했습니다. "
                            "부재 위치가 겹치거나 너무 가까운지 확인하세요.")
    res = b.Shape()
    out = []
    repaired = _repair_invalid_faces([s for m in members for s in m.shapes], b)
    for m in members:
        imgs = []
        for s in m.shapes:
            mod = occ.list_of_shape(b.Modified(s))
            if mod:
                imgs += mod
            elif not b.IsDeleted(s):
                imgs.append(s)
        kind = "face" if m.kind == "shell" else "edge"
        cast = occ.as_face if kind == "face" else occ.as_edge
        imgs = occ.unique([cast(s) for s in imgs if s.ShapeType() == occ.KIND[kind]])
        out.append(Member(m.name, m.kind, m.category, m.props, imgs))
    if repaired.get("still_invalid"):
        res, out, n_fixed = _fix_faces_reshape(res, out)
        repaired["faces_repaired"] += n_fixed
        repaired["still_invalid"] -= n_fixed
    FUSE_REPAIR.clear()
    FUSE_REPAIR.update(repaired)
    return res, out


def _geo_key(s):
    if s.ShapeType() == occ.KIND["face"]:
        c = occ.centroid(s)
        return ("f", round(c[0], 2), round(c[1], 2), round(c[2], 2), round(occ.area(s), 1))
    c = occ.centroid(s, 1)
    return ("e", round(c[0], 2), round(c[1], 2), round(c[2], 2), round(occ.length(s), 2))


def _fix_faces_reshape(res, members):
    """Second-stage repair (ShapeFix_Face, e.g. self-intersecting wires) of
    faces still invalid.  Replacements are propagated to the whole model with
    ShapeBuild_ReShape so neighbouring faces / beams stay connected; member
    lists are re-mapped (same shape, recorded replacement, or identical
    geometry)."""
    from OCP.ShapeBuild import ShapeBuild_ReShape
    from OCP.ShapeFix import ShapeFix_Face
    bad = [f for m in members if m.kind == "shell" for f in m.shapes if not occ.is_valid(f)]
    bad = occ.unique(bad)
    if not bad:
        return res, members, 0
    ctx = ShapeBuild_ReShape()
    for f in bad:
        sf = ShapeFix_Face()
        sf.SetContext(ctx)
        sf.Init(f)
        sf.Perform()
        nf = sf.Face()
        if not ctx.IsRecorded(f) and not nf.IsSame(f):
            ctx.Replace(f, nf)
    new_res = ctx.Apply(res)
    pool = {"face": occ.faces(new_res), "edge": occ.edges(new_res)}
    by_hash = {k: {} for k in pool}
    by_geo = {k: {} for k in pool}
    for k, lst in pool.items():
        for s in lst:
            by_hash[k].setdefault(s.__hash__(), []).append(s)
            by_geo[k].setdefault(_geo_key(s), []).append(s)
    out = []
    for m in members:
        kind = "face" if m.kind == "shell" else "edge"
        cast = occ.as_face if kind == "face" else occ.as_edge
        imgs = []
        for s in m.shapes:
            same = [t for t in by_hash[kind].get(s.__hash__(), []) if t.IsSame(s)]
            if same:
                imgs.append(same[0])
                continue
            if ctx.IsRecorded(s):
                v = ctx.Value(s)
                subs = occ.unique(occ.explore(v, kind)) if v.ShapeType() != occ.KIND[kind] else [cast(v)]
                found = []
                for t in subs:
                    found += [u for u in by_hash[kind].get(t.__hash__(), []) if u.IsSame(t)] or \
                        by_geo[kind].get(_geo_key(t), [])[:1]
                if found:
                    imgs += found
                    continue
            g = by_geo[kind].get(_geo_key(s), [])
            if g:
                imgs.append(g[0])
            else:
                imgs.append(s)       # should not happen; keeps the member complete
        out.append(Member(m.name, m.kind, m.category, m.props, occ.unique(imgs)))
    fixed = sum(1 for f in bad if all(occ.is_valid(t) for t in
                                      (occ.unique(occ.explore(ctx.Value(f), "face")) or [f])))
    return new_res, out, fixed


FUSE_REPAIR: Dict = {}


def _repair_invalid_faces(args, builder) -> Dict:
    """In-place repair (ShapeFix_Edge.FixSameParameter) of edges of faces that
    BRepCheck reports invalid after the fuse.  Topology / sharing unchanged."""
    from OCP.BRep import BRep_Tool
    from OCP.ShapeFix import ShapeFix_Edge
    faces = []
    for s in args:
        if s.ShapeType() != occ.KIND["face"]:
            continue
        mod = occ.list_of_shape(builder.Modified(s))
        faces += [occ.as_face(f) for f in (mod or [s])]
    bad = [f for f in occ.unique(faces) if not occ.is_valid(f)]
    sfe = ShapeFix_Edge()
    tol = 0.0
    for f in bad:
        for e in occ.edges(f):
            sfe.FixSameParameter(e)
            sfe.FixVertexTolerance(e)
            tol = max(tol, BRep_Tool.Tolerance_s(e))
    still = sum(1 for f in bad if not occ.is_valid(f))
    return {"faces_repaired": len(bad) - still, "still_invalid": still,
            "max_edge_tolerance": round(tol, 4)}
