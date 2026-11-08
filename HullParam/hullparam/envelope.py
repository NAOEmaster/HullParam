"""Hull envelope (outer skin) detection and the closed hull solid.

Input CAD of a shell model = outer skin faces + internal member faces.
Rules (generic, verified on GPPH A4-B1):

1. Every non-planar face is a skin candidate; planar faces are member candidates.
2. Skin candidates are sewn.  The largest sewn shell is the skin.
3. Each remaining free (closed) boundary of the skin is a hole.  If planar
   member faces exactly fill a hole (coplanar, inside, same area) they are
   moved to the skin (e.g. a flat transom drive plate or a flat deck).
   Otherwise the hole is capped with a planar face.
4. The watertight skin is turned into a solid, used for "inside hull" clipping.
5. Skin faces are classified as deck / side / bottom / chine / transom.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeSolid,
                                BRepBuilderAPI_Sewing)
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.ShapeFix import ShapeFix_Solid
from OCP.TopAbs import TopAbs_IN, TopAbs_ON
from OCP.gp import gp_Pnt, gp_Pnt2d

from . import occ
from .occ import GeometryError

SKIN_CLASSES = ("deck", "side", "bottom", "chine", "transom")


@dataclass
class Envelope:
    solid: object                       # TopoDS_Solid (closed hull)
    skin_faces: List[object]            # faces of the solid (outer skin)
    classes: List[str]                  # class per skin face
    info: Dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def bbox(self):
        return np.array(self.info["bbox"])

    def faces_of(self, *cls) -> list:
        return [f for f, c in zip(self.skin_faces, self.classes) if c in cls]

    def save(self, folder: str) -> None:
        os.makedirs(folder, exist_ok=True)
        occ.write_brep(self.solid, os.path.join(folder, "envelope.brep"))
        cents = [occ.centroid(f).round(3).tolist() for f in self.skin_faces]
        with open(os.path.join(folder, "envelope.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"info": self.info, "classes": self.classes,
                       "centroids": cents}, fh, indent=1)

    @staticmethod
    def load(folder: str) -> "Envelope":
        bp = os.path.join(folder, "envelope.brep")
        jp = os.path.join(folder, "envelope.json")
        if not (os.path.isfile(bp) and os.path.isfile(jp)):
            raise GeometryError(
                f"외판 데이터(envelope.brep/json)가 없습니다: {folder}. "
                "먼저 CAD에서 파라미터 추출(extract)을 실행하세요.")
        solid = occ.read_brep(bp)
        with open(jp, encoding="utf-8") as fh:
            meta = json.load(fh)
        fs = occ.faces(solid)
        if len(fs) != len(meta["classes"]):
            raise GeometryError("envelope.brep 과 envelope.json 이 일치하지 "
                                "않습니다 (면 개수 불일치). 다시 추출하세요.")
        # verify order with centroids (robust against traversal differences)
        cents = np.array(meta["centroids"])
        classes = []
        for f in fs:
            c = occ.centroid(f)
            i = int(np.argmin(np.linalg.norm(cents - c, axis=1)))
            classes.append(meta["classes"][i])
        sols = occ.unique(occ.explore(solid, "solid"))
        if len(sols) != 1:
            raise GeometryError("envelope.brep 에 선체 솔리드가 하나여야 합니다.")
        # make every skin edge "same parameter" (3-D curve == curves on faces)
        # in place; sewn seams otherwise give invalid pieces when split later
        from OCP.ShapeFix import ShapeFix_Edge
        sfe = ShapeFix_Edge()
        for f in fs:
            for e in occ.edges(f):
                sfe.FixSameParameter(e)
        env = Envelope(sols[0], fs, classes, meta["info"])
        if "seams" not in env.info:
            env.info["seams"] = seam_coordinates(fs)
        if "station_planes" not in env.info:
            env.info["station_planes"] = station_planes(fs)
        return env

    def station_plane(self, x: float, tol: float):
        """Exact (possibly slightly tilted) station planes of an existing
        transverse seam within ``tol`` of x:
        {'x': x0, 'port': (x0, a, b), 'stbd': (x0, a, b)}  with
        x = x0 + a*y + b*z on each side.  None if no seam is close."""
        best = None
        for sp in self.info.get("station_planes", []):
            d = abs(sp["x"] - x)
            if d <= tol and (best is None or d < abs(best["x"] - x)):
                best = sp
        return best

    def snap(self, axis: str, value: float, tol: float) -> float:
        """Nearest existing skin seam coordinate within tol (else value).
        Prevents sliver faces between a new member plane and an old seam."""
        vals = self.info.get("seams", {}).get(axis, [])
        if not vals or tol <= 0:
            return value
        a = np.asarray(vals)
        k = int(np.argmin(np.abs(a - value)))
        return float(a[k]) if abs(a[k] - value) <= tol else value


def station_planes(skin_faces, max_resid: float = 0.01) -> List[dict]:
    """Fit exact planes through transverse (≈x=const) skin seams, separately
    for port (y>0) and starboard (y<0):  x = x0 + a*y + b*z."""
    cand = []
    for e in occ.unique([e for f in skin_faces for e in occ.edges(f)]):
        pts = occ.edge_points(e, 15)
        if np.ptp(pts[:, 0]) < 3.0 and np.ptp(pts[:, 1]) + np.ptp(pts[:, 2]) > 20.0:
            cand.append(pts)
    if not cand:
        return []
    xs = [float(np.mean(p[:, 0])) for p in cand]
    order = np.argsort(xs)
    groups, cur = [], [order[0]]
    for i in order[1:]:
        if xs[i] - xs[cur[-1]] <= 3.0:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    out = []
    for g in groups:
        P = np.vstack([cand[i] for i in g])
        if np.ptp(P[:, 1]) + np.ptp(P[:, 2]) < 200:     # too short to be a station
            continue
        sides = {}
        ok = True
        for name, sel in (("port", P[:, 1] > 1e-6), ("stbd", P[:, 1] < -1e-6)):
            Q = P[sel]
            if len(Q) < 6 or np.ptp(Q[:, 1]) + np.ptp(Q[:, 2]) < 50:
                continue
            A = np.c_[np.ones(len(Q)), Q[:, 1], Q[:, 2]]
            coef, *_ = np.linalg.lstsq(A, Q[:, 0], rcond=None)
            res = np.abs(A @ coef - Q[:, 0]).max()
            if res > max_resid:
                ok = False
            sides[name] = [float(c) for c in coef]
        if not ok or not sides:
            continue
        if "port" not in sides:
            x0, a, b = sides["stbd"]
            sides["port"] = [x0, -a, b]
        if "stbd" not in sides:
            x0, a, b = sides["port"]
            sides["stbd"] = [x0, -a, b]
        x0 = 0.5 * (sides["port"][0] + sides["stbd"][0])
        out.append({"x": round(x0, 5), "port": sides["port"], "stbd": sides["stbd"]})
    return out


def seam_coordinates(skin_faces) -> Dict[str, List[float]]:
    """Exact coordinates of skin edges lying in x=, y= or z= planes."""
    out = {"x": [], "y": [], "z": []}
    for e in occ.unique([e for f in skin_faces for e in occ.edges(f)]):
        pts = occ.edge_points(e, 9)
        if np.linalg.norm(pts[-1] - pts[0]) < 1.0 and \
                np.linalg.norm(np.diff(pts, axis=0), axis=1).sum() < 5.0:
            continue
        for k, ax in enumerate("xyz"):
            if np.ptp(pts[:, k]) < 1e-3:
                out[ax].append(float(np.mean(pts[:, k])))
    for ax in out:
        vs = sorted(out[ax])
        uniq = []
        for v in vs:
            if not uniq or v - uniq[-1] > 1e-3:
                uniq.append(v)
        out[ax] = [round(v, 5) for v in uniq]
    return out


# ----------------------------------------------------------------------
def _free_wires(shape, tol):
    fb = ShapeAnalysis_FreeBounds(shape, tol, False, False)
    closed = occ.explore(fb.GetClosedWires(), "wire")
    opened = occ.explore(fb.GetOpenWires(), "wire")
    return closed, opened


def _face_inside_cap(face, cap, tol) -> bool:
    """True if a planar face lies in the cap plane and inside the cap."""
    ok, n, o = occ.plane_info(face)
    okc, nc, oc = occ.plane_info(cap)
    if not (ok and okc):
        return False
    if abs(abs(float(n @ nc)) - 1.0) > 1e-4:
        return False
    if abs(float((o - oc) @ nc)) > tol:
        return False
    c = occ.centroid(face)
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    surf = BRep_Tool.Surface_s(cap)
    pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*c), surf)
    if pr.NbPoints() == 0:
        return False
    u, v = pr.LowerDistanceParameters()
    st = BRepClass_FaceClassifier(cap, gp_Pnt2d(u, v), tol).State()
    return st in (TopAbs_IN, TopAbs_ON)


def build_envelope(shape, sew_tol: float = 0.5, log=print):
    """Split an input CAD shape into (Envelope, member_faces)."""
    all_faces = occ.faces(shape)
    if not all_faces:
        raise GeometryError("입력 CAD에 면(face)이 없습니다.")
    skin, planar = [], []
    for f in all_faces:
        (planar if occ.plane_info(f)[0] else skin).append(f)
    log(f"  면 {len(all_faces)}개: 곡면 {len(skin)}개(외판 후보), "
        f"평면 {len(planar)}개(부재 후보)")
    if not skin:
        raise GeometryError("곡면(외판) 후보가 없습니다. 선체 외판이 포함된 "
                            "전체 CAD인지 확인하세요.")

    sew = BRepBuilderAPI_Sewing(sew_tol)
    for f in skin:
        sew.Add(f)
    sew.Perform()
    sewn = sew.SewedShape()
    shells = occ.unique(occ.explore(sewn, "shell"))
    if not shells:
        raise GeometryError("외판 면을 하나의 껍데기로 꿰매지 못했습니다.")
    shells.sort(key=lambda s: -occ.area(s))
    main = shells[0]
    extra_curved = [f for s in shells[1:] for f in occ.faces(s)]
    if extra_curved:
        log(f"  외판에 연결되지 않은 곡면 {len(extra_curved)}개 → 내부 부재로 취급")

    closed, opened = _free_wires(main, sew_tol)
    if opened:
        bb = occ.bbox(occ.compound(opened))
        raise GeometryError(
            "외판이 닫혀 있지 않습니다(열린 경계 "
            f"{len(opened)}개, 범위 x[{bb[0]:.0f},{bb[3]:.0f}] "
            f"y[{bb[1]:.0f},{bb[4]:.0f}] z[{bb[2]:.0f},{bb[5]:.0f}]). "
            "꿰매기 공차(sew_tol)를 늘리거나 CAD를 확인하세요.")

    members = list(planar)
    sew2 = BRepBuilderAPI_Sewing(sew_tol)
    sew2.Add(main)
    filled = []
    for w in closed:
        mf = BRepBuilderAPI_MakeFace(w, True)
        if not mf.IsDone():
            bb = occ.bbox(w)
            raise GeometryError(
                "외판 구멍이 평면이 아니어서 막을 수 없습니다: "
                f"x[{bb[0]:.0f},{bb[3]:.0f}] y[{bb[1]:.0f},{bb[4]:.0f}] "
                f"z[{bb[2]:.0f},{bb[5]:.0f}]")
        cap = mf.Face()
        a_cap = occ.area(cap)
        cand = [f for f in members if _face_inside_cap(f, cap, sew_tol)]
        a_c = sum(occ.area(f) for f in cand)
        if cand and abs(a_c - a_cap) <= 0.01 * a_cap + 1.0:
            for f in cand:
                sew2.Add(f)
                filled.append(f)
            members = [f for f in members
                       if not any(f.IsSame(c) for c in cand)]
            log(f"  외판 구멍(면적 {a_cap/1e6:.3f} m²)을 평면 부재 "
                f"{len(cand)}개로 채움 (외판에 포함)")
        else:
            sew2.Add(cap)
            log(f"  외판 구멍(면적 {a_cap/1e6:.3f} m²)을 평면으로 막음")
    sew2.Perform()
    skin_shell = sew2.SewedShape()
    shells = occ.unique(occ.explore(skin_shell, "shell"))
    if len(shells) != 1:
        raise GeometryError(f"외판 꿰매기 결과 껍데기가 {len(shells)}개입니다.")
    c2, o2 = _free_wires(shells[0], sew_tol)
    if c2 or o2:
        raise GeometryError("외판을 완전히 닫지 못했습니다(남은 자유 경계 "
                            f"{len(c2) + len(o2)}개).")

    ms = BRepBuilderAPI_MakeSolid(occ.as_shell(shells[0]))
    if not ms.IsDone():
        raise GeometryError("외판으로 선체 솔리드를 만들지 못했습니다.")
    fix = ShapeFix_Solid(ms.Solid())
    fix.Perform()
    solid = occ.as_solid(occ.explore(fix.Solid(), "solid")[0])
    vol = occ.volume(solid)
    if vol <= 0:
        raise GeometryError("선체 솔리드 부피가 0 이하입니다(방향 오류).")
    if not occ.is_valid(solid):
        log("  [경고] 선체 솔리드가 BRepCheck 기준 완전하지 않습니다 "
            "(내부 판정에는 사용 가능)")

    skin_faces = occ.faces(solid)
    bb = occ.bbox(solid)
    classes = classify_skin(skin_faces, bb)
    info = {
        "bbox": bb.round(4).tolist(),
        "volume_m3": vol / 1e9,
        "skin_area_m2": sum(occ.area(f) for f in skin_faces) / 1e6,
        "n_skin_faces": len(skin_faces),
        "sew_tol": sew_tol,
        "z_deck": float(bb[5]),
        "z_keel": float(bb[2]),
        "x_aft": float(bb[0]),
        "x_fwd": float(bb[3]),
        "half_beam": float(max(abs(bb[1]), abs(bb[4]))),
        "seams": seam_coordinates(skin_faces),
        "station_planes": station_planes(skin_faces),
    }
    cnt = {c: classes.count(c) for c in SKIN_CLASSES}
    log("  외판 면 분류: " + ", ".join(f"{k} {v}" for k, v in cnt.items()))
    log(f"  선체 솔리드: 부피 {vol/1e9:.2f} m³, 외판 {info['skin_area_m2']:.1f} m²")
    member_faces = members + extra_curved
    return Envelope(solid, skin_faces, classes, info), member_faces


def classify_skin(skin_faces, bb) -> List[str]:
    """deck / side / bottom / chine / transom from the outward normal."""
    zmin, zmax = bb[2], bb[5]
    depth = zmax - zmin
    half_b = max(abs(bb[1]), abs(bb[4]))
    length = bb[3] - bb[0]
    out = []
    for f in skin_faces:
        c = occ.centroid(f)
        n = occ.face_normal(f, c)
        if n[0] < -0.7 and c[0] < bb[0] + 0.3 * length:
            out.append("transom")
        elif n[2] > 0.9 and c[2] > zmax - 0.05 * depth:
            out.append("deck")
        elif n[2] < -0.97 and abs(c[1]) > 0.6 * half_b and \
                c[2] > zmin + 0.1 * depth:
            out.append("chine")
        elif n[2] < -0.5:
            out.append("bottom")
        else:
            out.append("side")
    return out
