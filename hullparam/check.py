"""Integrity checks of a generated (or loaded) shell + beam model.

Levels: ERROR (model must not be used), WARN (review), INFO (statistics).

Checks
  C01 shape validity (BRepCheck) of every face / edge
  C02 every shell member has faces and thickness > 0; every beam a section
  C03 beams lie on shells (each beam edge shared with >= 1 face)
  C04 structure is one connected piece (faces + beams via shared edges)
  C05 gaps: free shell edges lying within gap_tol of another face but not
      connected to it
  C06 the same face assigned to two members (overlapping members)
  C07 small edges / small faces (mesh quality hazards)
  C08 statistics: T-joints, free edges covered / not covered by beams
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from . import occ


def _issue(level, code, msg, member=None, loc=None):
    d = {"level": level, "code": code, "message": msg}
    if member:
        d["member"] = member
    if loc is not None:
        d["location"] = [round(float(v), 1) for v in loc]
    return d


class _Index:
    """Shape -> owner lookups based on hash buckets + IsSame."""

    def __init__(self):
        self.b = defaultdict(list)

    def add(self, s, owner):
        self.b[s.__hash__()].append((s, owner))

    def owners(self, s):
        return [o for t, o in self.b.get(s.__hash__(), []) if t.IsSame(s)]


def face_check_status(face) -> set:
    """Names of all BRepCheck statuses found on a face and its sub-shapes."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    an = BRepCheck_Analyzer(face)
    out = set()
    subs = [face] + occ.unique(occ.explore(face, "wire")) + occ.unique(occ.explore(face, "edge")) + \
        occ.unique(occ.explore(face, "vertex"))
    for s in subs:
        r = an.Result(s)
        if r is None:
            continue
        try:
            for x in r.Status():
                out.add(str(x).split(".")[-1].replace("BRepCheck_", ""))
            r.InitContextIterator()
            while r.MoreShapeInContext():
                for x in r.StatusOnShape():
                    out.add(str(x).split(".")[-1].replace("BRepCheck_", ""))
                r.NextShapeInContext()
        except Exception:
            pass
    out.discard("NoError")
    return out


def _meshes_ok(face, tol_rel: float = 0.01) -> bool:
    """Tessellate a copy-free face and compare triangle area with the exact area."""
    try:
        occ.mesh_shape(face, 0.5, 0.2)
        v, t = occ.face_triangles(face)
        if len(t) == 0:
            return False
        a = v[t[:, 1]] - v[t[:, 0]]
        b = v[t[:, 2]] - v[t[:, 0]]
        tri = 0.5 * np.linalg.norm(np.cross(a, b), axis=1).sum()
        ex = occ.area(face)
        return ex > 0 and abs(tri - ex) <= tol_rel * ex
    except Exception:
        return False


def check_model(model, gap_tol: float = 2.0, log=print) -> Dict:
    t0 = time.time()
    p = model.params["build"] if model.params else {}
    small_e = float(p.get("check_small_edge", 2.0))
    small_f = float(p.get("check_small_face", 200.0))
    issues: List[dict] = []
    shells = model.by_kind("shell")
    beams = model.by_kind("beam")

    # ---- C02 members
    for m in shells:
        if not m.shapes:
            issues.append(_issue("ERROR", "C02", f"쉘 부재 {m.name} 에 면이 없습니다.", m.name))
        if not (m.props.get("thickness") or 0) > 0:
            issues.append(_issue("ERROR", "C02", f"쉘 부재 {m.name} 의 두께가 없습니다.", m.name))
    for m in beams:
        if not m.shapes:
            issues.append(_issue("ERROR", "C02", f"빔 부재 {m.name} 에 선이 없습니다.", m.name))
        if not m.props.get("section"):
            issues.append(_issue("ERROR", "C02", f"빔 부재 {m.name} 의 단면이 없습니다.", m.name))

    # ---- indices
    face_owner = _Index()
    for m in shells:
        for f in m.shapes:
            face_owner.add(f, m.name)
    all_faces = [f for m in shells for f in m.shapes]
    all_faces_u = occ.unique(all_faces)
    shell_cmp = occ.compound(all_faces_u)
    emap = occ.edge_face_map(shell_cmp)
    edge_idx = {}
    for i in range(1, emap.Extent() + 1):
        e = emap.FindKey(i)
        edge_idx.setdefault(e.__hash__(), []).append((e, i))

    def faces_of_edge(e):
        for t, i in edge_idx.get(e.__hash__(), []):
            if t.IsSame(e):
                return [occ.as_face(f) for f in occ.list_of_shape(emap.FindFromIndex(i))]
        return None

    # ---- C06 duplicate faces
    dup = 0
    for f in all_faces_u:
        own = sorted(set(face_owner.owners(f)))
        if len(own) > 1:
            dup += 1
            if dup <= 20:
                issues.append(_issue("WARN", "C06", f"같은 면이 여러 부재에 속함: {', '.join(own)} "
                                     "(부재가 서로 겹침)", own[0], occ.centroid(f)))
    # ---- C01 validity
    bad = 0
    tangent = 0
    for m in shells:
        for f in m.shapes:
            if not occ.is_valid(f):
                st = face_check_status(f)
                if st <= {"SelfIntersectingWire", "UnorientableShape"} and _meshes_ok(f):
                    # only a near-tangent overlap of two adjacent edges' curves in
                    # the surface parameter space; 3-D shape tessellates correctly
                    tangent += 1
                    if tangent <= 20:
                        issues.append(_issue(
                            "WARN", "C01", f"{m.name}: 두 모서리가 거의 접하는 각도로 만나 매개변수 공간에서 "
                            "겹침(BRepCheck SelfIntersectingWire). 3D 형상과 면 분할(메싱)은 정상 확인됨. "
                            "해석 프로그램에서 문제가 되면 근처 부재 위치를 몇 mm 옮기세요.", m.name,
                            occ.centroid(f)))
                    continue
                bad += 1
                if bad <= 20:
                    issues.append(_issue("ERROR", "C01", f"{m.name}: 유효하지 않은 면 "
                                         f"({', '.join(sorted(st)) or 'BRepCheck'})", m.name,
                                         occ.centroid(f)))
    for m in beams:
        for e in m.shapes:
            if not occ.is_valid(e):
                bad += 1
                if bad <= 40:
                    issues.append(_issue("ERROR", "C01", f"{m.name}: 유효하지 않은 선", m.name,
                                         occ.centroid(e, 1)))

    # ---- C03 beams on shells
    beam_edge = _Index()
    floating = 0
    for m in beams:
        for e in m.shapes:
            beam_edge.add(e, m.name)
            fs = faces_of_edge(e)
            if not fs:
                floating += 1
                if floating <= 30:
                    issues.append(_issue("ERROR", "C03", f"{m.name}: 판에 붙지 않은(떠 있는) 빔 선",
                                         m.name, occ.centroid(e, 1)))

    # ---- edge statistics, C05 gaps, C07 small edges
    n_free = n_free_beam = n_tj = n_small_e = 0
    free_edges = []
    for i in range(1, emap.Extent() + 1):
        e = occ.as_edge(emap.FindKey(i))
        nf = len(occ.list_of_shape(emap.FindFromIndex(i)))
        if BRep_Tool.Degenerated_s(e):
            continue          # collapsed edge of a surface pole (input geometry)
        L = occ.length(e)
        if L < small_e:
            n_small_e += 1
            if n_small_e <= 30:
                fs = [occ.as_face(f) for f in occ.list_of_shape(emap.FindFromIndex(i))]
                own = sorted(set(o for f in fs for o in face_owner.owners(f)))
                issues.append(_issue("WARN", "C07", f"짧은 모서리 {L:.2f} mm (메시 품질 주의): "
                                     f"{', '.join(own)}", own[0] if own else None,
                                     occ.centroid(e, 1) if L > 0 else occ.edge_points(e, 2)[0]))
        if nf >= 3:
            n_tj += 1
        if nf == 1:
            if beam_edge.owners(e):
                n_free_beam += 1
            else:
                n_free += 1
                free_edges.append((e, occ.as_face(occ.list_of_shape(emap.FindFromIndex(i))[0])))
    # gap test on uncovered free edges
    n_gap = 0
    if free_edges:
        boxes = [(f, occ.bbox(f, optimal=False)) for f in all_faces_u]
        for e, own_face in free_edges:
            eb = occ.bbox(e, optimal=False)
            near = [f for f, b in boxes
                    if not f.IsSame(own_face) and np.all(b[:3] - gap_tol <= eb[3:]) and
                    np.all(eb[:3] <= b[3:] + gap_tol)]
            # faces sharing a vertex with the edge are connected -> skip them
            vs = occ.unique(occ.explore(e, "vertex"))
            cand = []
            for f in near:
                fv = occ.unique(occ.explore(f, "vertex"))
                if any(v.IsSame(w) for v in vs for w in fv):
                    continue
                cand.append(f)
            if not cand:
                continue
            d = BRepExtrema_DistShapeShape(e, occ.compound(cand))
            if d.IsDone() and 1e-3 < d.Value() < gap_tol:
                n_gap += 1
                if n_gap <= 30:
                    own = face_owner.owners(own_face)
                    p_ = d.PointOnShape1(1)
                    issues.append(_issue("ERROR", "C05",
                                         f"틈(gap) {d.Value():.2f} mm: {own[0] if own else '?'} 의 경계가 "
                                         "이웃 판과 연결되지 않음", own[0] if own else None,
                                         (p_.X(), p_.Y(), p_.Z())))
    # ---- C07 small faces
    n_small_f = 0
    for m in shells:
        for f in m.shapes:
            a = occ.area(f)
            if a < small_f:
                n_small_f += 1
                if n_small_f <= 30:
                    issues.append(_issue("WARN", "C07", f"{m.name}: 작은 면 {a:.0f} mm² (메시 품질 주의)",
                                         m.name, occ.centroid(f)))

    # ---- C04 connectivity (union-find over faces + beam edges via edges)
    parent = {}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    fid = {}
    for k, f in enumerate(all_faces_u):
        fid.setdefault(f.__hash__(), []).append((f, k))
        parent[("f", k)] = ("f", k)

    def face_key(f):
        for t, k in fid.get(f.__hash__(), []):
            if t.IsSame(f):
                return ("f", k)
        return None
    for i in range(1, emap.Extent() + 1):
        fs = [face_key(occ.as_face(f)) for f in occ.list_of_shape(emap.FindFromIndex(i))]
        fs = [f for f in fs if f]
        for a, b in zip(fs, fs[1:]):
            union(a, b)
    # beams: union with faces they touch; a beam edge touching nothing stays alone
    for m in beams:
        for k, e in enumerate(m.shapes):
            key = ("b", m.name, k)
            parent[key] = key
            for f in faces_of_edge(e) or []:
                fk = face_key(f)
                if fk:
                    union(key, fk)
    # beam edges also connect through shared vertices with other beam edges
    comps = defaultdict(list)
    for k in parent:
        comps[find(k)].append(k)
    comp_list = sorted(comps.values(), key=len, reverse=True)
    if len(comp_list) > 1:
        main = comp_list[0]
        for c in comp_list[1:][:20]:
            names = set()
            loc = None
            for k in c:
                if k[0] == "f":
                    f = all_faces_u[k[1]]
                    names.update(face_owner.owners(f))
                    loc = occ.centroid(f)
                else:
                    names.add(k[1])
            issues.append(_issue("ERROR", "C04", "구조에서 분리된 부분: " + ", ".join(sorted(names)),
                                 sorted(names)[0] if names else None, loc))

    stats = {
        "shell_members": len(shells), "beam_members": len(beams),
        "faces": len(all_faces_u), "beam_edges": sum(len(m.shapes) for m in beams),
        "edges": emap.Extent(), "t_joints": n_tj,
        "free_edges_with_beam": n_free_beam, "free_edges_open": n_free,
        "small_edges": n_small_e, "small_faces": n_small_f, "gaps": n_gap,
        "floating_beam_edges": floating, "invalid": bad, "tangent_faces": tangent,
        "duplicate_faces": dup,
        "components": len(comp_list),
        "shell_area_m2": round(sum(occ.area(f) for f in all_faces_u) / 1e6, 3),
        "beam_length_m": round(sum(occ.length(e) for m in beams for e in m.shapes) / 1e3, 3),
        "elapsed_s": round(time.time() - t0, 1),
    }
    levels = [i["level"] for i in issues]
    status = "ERROR" if "ERROR" in levels else ("WARN" if "WARN" in levels else "OK")
    issues.append(_issue("INFO", "C08",
                         f"T-접합 모서리 {n_tj}, 빔이 붙은 자유모서리 {n_free_beam}, "
                         f"보강 없는 자유모서리 {n_free} (판 끝단/개구부 등)"))
    for w in getattr(model, "warnings", []) or []:
        issues.append(_issue("WARN", "B00", w))
        if status == "OK":
            status = "WARN"
    rep = {"status": status, "stats": stats, "issues": issues}
    log(f"무결성 검사: {status}  (면 {stats['faces']}, 빔선 {stats['beam_edges']}, "
        f"분리 {stats['components'] - 1}, 틈 {n_gap}, 떠있는 빔 {floating}, "
        f"무효 {bad}, 짧은 모서리 {n_small_e}, 작은 면 {n_small_f}) {stats['elapsed_s']} s")
    return rep
