"""Automatic parameterisation of a full hull-structure CAD model.

    shape  ->  envelope (skin, solid)  +  members
           ->  stations (bulkheads / web frames), girders, plates,
               stringers (from skin seams), centre-line beams
           ->  params.yaml  (+ envelope.brep/json, extract_report.json)

Optional COMSOL .mph import provides thickness, beam sections and the
bulkhead / transom stiffener lines that are not visible in the CAD.
"""
from __future__ import annotations

import copy
import json
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np
from shapely.geometry import box as sbox
from shapely.ops import unary_union

from . import comsol, occ
from . import params as P
from . import profile2d as p2
from .envelope import Envelope, build_envelope

REF_TOL = 3.0   # mm: snap x limits to a station reference


def _cluster(values: List[float], tol: float) -> List[List[int]]:
    order = np.argsort(values)
    groups: List[List[int]] = []
    for i in order:
        if groups and abs(values[i] - values[groups[-1][-1]]) <= tol:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    return groups


def _ref(x: float, stations: List[dict], hull: dict, end: str):
    """Number -> 'BHn'/'Fn'/'aft'/'fwd' when within REF_TOL."""
    for s in stations:
        if abs(s["x"] - x) <= REF_TOL:
            return s["name"]
    if end == "from" and x <= hull["x_aft"] + 250:
        return "aft"
    if end == "to" and x >= hull["x_fwd"] - 5:
        return "fwd"
    return round(float(x), 1)


# ----------------------------------------------------------------------
def extract(cad_path: str, out_dir: str, mph_path: Optional[str] = None,
            sew_tol: float = 0.5, name: Optional[str] = None, log=print):
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    log(f"[1/6] CAD 읽기: {os.path.basename(cad_path)}")
    shape = occ.read_cad(cad_path)
    log("[2/6] 외판(envelope) 인식")
    env, members = build_envelope(shape, sew_tol, log=log)
    env.save(out_dir)
    hull = env.info
    report: Dict = {"source": os.path.abspath(cad_path), "hull": hull,
                    "warnings": [], "members": {}}

    # ------------------------------------------------------------ classify
    xf, yf, zf, other = [], [], [], []
    for f in members:
        ok, n, o = occ.plane_info(f)
        if not ok:
            other.append(f)
            continue
        n = np.abs(n)
        if n[0] > 0.999:
            xf.append(f)
        elif n[1] > 0.999:
            yf.append(f)
        elif n[2] > 0.999:
            zf.append(f)
        else:
            other.append(f)
    log(f"[3/6] 부재 면 분류: x-평면 {len(xf)}, y-평면 {len(yf)}, "
        f"z-평면 {len(zf)}, 기타 {len(other)}")

    # ------------------------------------------------------------ stations
    xs = [occ.centroid(f)[0] for f in xf]
    # exact plane position at y=0, z=0 (frames may be tilted by a tiny angle)
    x0s = []
    for f in xf:
        _, n, o = occ.plane_info(f)
        x0s.append(float(n @ o) / n[0])
    stations = []
    for grp in _cluster(xs, 5.0):
        fs = [xf[i] for i in grp]
        a = np.array([occ.area(f) for f in fs])
        x = float(np.average([x0s[i] for i in grp], weights=a))
        sec = occ.common(occ.plane_face("x", x), env.solid)
        S = p2.shape_to_polygon(sec, "x")
        M = unary_union([p2.face_to_polygon(f, "x") for f in fs]).buffer(0)
        ratio = M.area / S.area if S.area else 0
        st = {"x": round(x, 1), "S": S, "M": M, "ratio": ratio,
              "cad_area_m2": round(M.area / 1e6, 4)}
        if ratio >= 0.97:
            st["kind"] = "bulkhead"
        else:
            st["kind"] = "frame"
            st.update(_fit_frame(S, M))
        stations.append(st)
    bhs = [s for s in stations if s["kind"] == "bulkhead"]
    frs = [s for s in stations if s["kind"] == "frame"]
    for i, s in enumerate(bhs):
        s["name"] = f"BH{i+1}"
    for i, s in enumerate(frs):
        s["name"] = f"F{i+1}"
    log(f"[4/6] 횡부재: 격벽 {len(bhs)}개 {[s['x'] for s in bhs]}, "
        f"늑골 {len(frs)}개 {[s['x'] for s in frs]}")
    for s in frs:
        if s.get("iou", 1) < 0.85:
            report["warnings"].append(
                f"{s['name']} (x={s['x']}) 형상이 웨브 프레임 모델과 잘 맞지 않습니다 "
                f"(IoU {s['iou']:.2f}).")

    frames_cfg = _frames_mode(bhs, frs, hull, report)

    # ------------------------------------------------------------ girders
    girders = _girders(yf, env, stations, hull, report)
    plates = _plates(zf, env, stations, hull, report)
    log(f"      거더 {len(girders)}종, 수평판 {len(plates)}개")

    # ------------------------------------------------------------ fixed
    if other:
        occ.write_brep(occ.compound(other), os.path.join(out_dir, "fixed_members.brep"))
        report["warnings"].append(
            f"자동 인식되지 않은 부재 면 {len(other)}개는 그대로 유지됩니다 "
            "(fixed_members.brep).")

    # ------------------------------------------------------------ seams
    log("[5/6] 외판 분할선에서 stringer / 중심선 보강재 추출")
    gabs = sorted(set(round(abs(g["y"]), 1) for g in girders))
    seams = _seams(env, gabs, hull, report)

    # ------------------------------------------------------------ COMSOL
    ov = None
    if mph_path:
        log(f"[6/6] COMSOL 파라미터 가져오기: {os.path.basename(mph_path)}")
        try:
            ov = comsol.gpph_overrides(comsol.read_parameters(mph_path))
        except Exception as e:  # pragma: no cover - report and continue
            report["warnings"].append(f"COMSOL 파일을 읽지 못했습니다: {e}")
    else:
        log("[6/6] COMSOL 파일 없음 → 두께/단면은 기본값 (params.yaml 에서 수정)")

    params = _assemble(name or os.path.splitext(os.path.basename(cad_path))[0],
                       cad_path, mph_path, hull, bhs, frs, frames_cfg, girders,
                       plates, seams, ov, report, sew_tol)
    P.save(params, os.path.join(out_dir, "params.yaml"))
    # round-trip check: the parameters must expand without error
    lay = P.expand(P.load(os.path.join(out_dir, "params.yaml")), hull)
    report["warnings"] += lay.warnings
    report["elapsed_s"] = round(time.time() - t0, 1)
    report["stations"] = [{k: v for k, v in s.items() if k not in ("S", "M")}
                          for s in stations]
    with open(os.path.join(out_dir, "extract_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False, default=float)
    log(f"완료 ({report['elapsed_s']} s): {os.path.join(out_dir, 'params.yaml')}")
    for w in report["warnings"]:
        log("  [주의] " + w)
    return params, report


# ----------------------------------------------------------------------
def _fit_frame(S, M) -> dict:
    """Web depth / deck beam depth / corner radius that best reproduce the
    CAD frame (max IoU)."""
    m = p2.measure_frame(S, M)
    if not m:
        return {"web_depth": 200.0, "deck_beam_depth": 100.0,
                "corner_radius": 0.0, "iou": 0.0}
    z_deck = S.bounds[3]
    d0, db = m["web_depth"], m["deck_beam_depth"]
    best = (-1, d0, 0.0)
    for r in (0, 50, 100, 150, 200, 250, 300):
        for d in np.arange(max(20, d0 - 40), d0 + 41, 5.0):
            O = p2.frame_opening(S, d, db, z_deck, r)
            v = p2.iou(S.difference(O), M)
            if v > best[0]:
                best = (v, float(d), float(r))
    v, d, r = best
    for dd in np.arange(d - 4, d + 4.1, 1.0):
        O = p2.frame_opening(S, dd, db, z_deck, r)
        vv = p2.iou(S.difference(O), M)
        if vv > v:
            v, d = vv, float(dd)
    return {"web_depth": round(d, 1), "deck_beam_depth": round(db, 1),
            "corner_radius": round(r, 1), "iou": round(v, 4)}


def _frames_mode(bhs, frs, hull, report) -> dict:
    fx = [s["x"] for s in frs]
    bx = [s["x"] for s in bhs]
    cfg = {"mode": "list", "positions": fx, "x_aft_ref": 0.0,
           "x_fwd_ref": round(hull["x_fwd"], 1)}
    if not fx:
        cfg["mode"] = "per_compartment"
        cfg["per_compartment"] = [0] * (len(bx) + 1)
        return cfg
    comp = [[] for _ in range(len(bx) + 1)]
    for x in fx:
        k = sum(1 for b in bx if b < x)
        comp[k].append(x)
    # aft reference
    c0 = comp[0]
    if len(c0) >= 2:
        aref = c0[0] - float(np.median(np.diff(c0)))
    elif len(c0) == 1:
        aref = 2 * c0[0] - (bx[0] if bx else hull["x_fwd"])
    else:
        aref = 0.0
    cl = comp[-1]
    if len(cl) >= 2:
        fref = cl[-1] + float(np.median(np.diff(cl)))
    elif len(cl) == 1:
        fref = 2 * cl[0] - (bx[-1] if bx else aref)
    else:
        fref = hull["x_fwd"]
    bounds = [aref] + bx + [fref]
    ok = hull["x_aft"] - 500 < aref < (bx[0] if bx else fref) and \
        (bx[-1] if bx else aref) < fref < hull["x_fwd"] + 500
    for k, xs in enumerate(comp):
        a, b = bounds[k], bounds[k + 1]
        for j, x in enumerate(xs):
            if abs(a + (b - a) * (j + 1) / (len(xs) + 1) - x) > 2.0:
                ok = False
    if ok:
        cfg = {"mode": "per_compartment", "x_aft_ref": round(aref, 1),
               "x_fwd_ref": round(fref, 1),
               "per_compartment": [len(c) for c in comp], "positions": fx}
        report["frames_rule"] = (
            "늑골이 각 구획(트랜섬 기준점–격벽–선수 기준점)을 균등 분할함 → "
            f"per_compartment {cfg['per_compartment']}")
    else:
        report["frames_rule"] = "늑골 간격이 구획 균등 분할이 아님 → list 모드"
    return cfg


def _girders(yf, env, stations, hull, report) -> List[dict]:
    if not yf:
        return []
    ys = [occ.centroid(f)[1] for f in yf]
    out = []
    for grp in _cluster(ys, 2.0):
        y = float(np.mean([ys[i] for i in grp]))
        segs = []
        for i in grp:
            poly = p2.face_to_polygon(yf[i], "y")
            x0, _, x1, _ = poly.bounds
            segs.append([float(x0), float(x1), _top_profile(poly)])
        segs.sort(key=lambda s: s[0])
        merged = []
        for s in segs:
            if merged and s[0] - merged[-1][1] < 2.0 and \
                    abs(_zat(merged[-1][2], merged[-1][1]) - _zat(s[2], s[0])) < 1.0:
                merged[-1] = [merged[-1][0], s[1], _join_top(merged[-1][2], s[2])]
            else:
                merged.append(list(s))
        # recon quality
        M = unary_union([p2.face_to_polygon(yf[i], "y") for i in grp]).buffer(0)
        R = []
        for x0, x1, zt in merged:
            rect = p2.polygon_to_face(girder_polygon(x0, x1, zt, hull["z_keel"] - 10), "y", y)
            R.append(p2.shape_to_polygon(occ.common(rect, env.solid), "y"))
        iou = p2.iou(unary_union(R), M) if R else 0
        out.append({"y": round(y, 1), "segments": merged, "iou": round(iou, 4)})
    # pair symmetric
    res = []
    used = set()
    for i, g in enumerate(out):
        if i in used:
            continue
        mate = None
        for j, h in enumerate(out):
            if j != i and j not in used and abs(h["y"] + g["y"]) < 2.0 and \
                    len(h["segments"]) == len(g["segments"]) and \
                    all(abs(s[0] - t[0]) < 5 and abs(s[1] - t[1]) < 5 and
                        abs(_zat(s[2], (s[0] + s[1]) / 2) - _zat(t[2], (t[0] + t[1]) / 2)) < 2
                        for s, t in zip(g["segments"], h["segments"])):
                mate = j
                break
        used.add(i)
        sym = mate is not None
        if sym:
            used.add(mate)
        yy = abs(g["y"]) if sym else g["y"]
        segs = [{"x_from": _ref(a, stations, hull, "from"),
                 "x_to": _ref(b, stations, hull, "to"), "z_top": zt}
                for a, b, zt in g["segments"]]
        if g["iou"] < 0.9:
            report["warnings"].append(f"거더 y={g['y']} 재구성 일치도 낮음 (IoU {g['iou']:.2f})")
        res.append({"y": round(yy, 1), "symmetric": sym, "segments": segs,
                    "iou": g["iou"]})
    res.sort(key=lambda g: abs(g["y"]))
    return res


def _top_profile(poly, tol: float = 0.5):
    """Upper boundary z(x) of a girder polygon -> number or {x, value} table."""
    from shapely.geometry import LineString
    x0, z0, x1, z1 = poly.bounds
    xs = np.linspace(x0 + 0.01, x1 - 0.01, max(20, int((x1 - x0) / 10)))
    top = []
    for x in xs:
        g = poly.intersection(LineString([(x, z0 - 1), (x, z1 + 1)]))
        top.append(g.bounds[3] if not g.is_empty else np.nan)
    top = np.array(top)
    ok = ~np.isnan(top)
    xs, top = xs[ok], top[ok]
    if np.ptp(top) < 1.0:
        return round(float(np.max(top)), 1)
    ls = LineString(np.c_[xs, top]).simplify(tol)
    pts = np.asarray(ls.coords)
    pts[0, 0], pts[-1, 0] = x0, x1
    return {"x": [round(float(a), 1) for a in pts[:, 0]],
            "value": [round(float(b), 1) for b in pts[:, 1]]}


def _zat(zt, x):
    return P.interp(zt, x, "z_top")


def _join_top(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) < 1:
        return a
    xa, va = (a["x"], a["value"]) if isinstance(a, dict) else ([], [])
    xb, vb = (b["x"], b["value"]) if isinstance(b, dict) else ([], [])
    if not xa:
        xa, va = [xb[0] - 1.0 if xb else 0.0], [float(a)]
    if not xb:
        xb, vb = [xa[-1] + 1.0], [float(b)]
    xs, vs = list(xa) + list(xb), list(va) + list(vb)
    # keep monotonically increasing x
    out_x, out_v = [], []
    for x, v in sorted(zip(xs, vs)):
        if out_x and abs(x - out_x[-1]) < 0.5:
            continue
        out_x.append(x)
        out_v.append(v)
    return {"x": out_x, "value": out_v}


def girder_polygon(x0, x1, z_top, z_low):
    """(x,z) polygon of a girder segment with a (possibly varying) top."""
    from shapely.geometry import Polygon
    if isinstance(z_top, (int, float)):
        return Polygon([(x0, z_low), (x1, z_low), (x1, z_top), (x0, z_top)])
    xs = [x0] + [x for x in z_top["x"] if x0 < x < x1] + [x1]
    top = [(x, P.interp(z_top, x)) for x in xs]
    return Polygon([(x0, z_low), (x1, z_low)] + top[::-1])


def _plates(zf, env, stations, hull, report) -> List[dict]:
    out = []
    for f in zf:
        bb = occ.bbox(f)
        out.append({"z": round(float(bb[2]), 1),
                    "x_from": _ref(bb[0], stations, hull, "from"),
                    "x_to": _ref(bb[3], stations, hull, "to"),
                    "y_from": round(float(bb[1]), 1), "y_to": round(float(bb[4]), 1),
                    "area_m2": round(occ.area(f) / 1e6, 4)})
        rect_a = (bb[3] - bb[0]) * (bb[4] - bb[1])
        if rect_a > 0 and occ.area(f) / rect_a < 0.95:
            report["warnings"].append(
                f"수평판 z={bb[2]:.0f} 이 사각형이 아닙니다 (사각형으로 근사, "
                f"면적비 {occ.area(f)/rect_a:.2f})")
    return out


def _seams(env: Envelope, girders_abs, hull, report) -> dict:
    """Smooth constant-coordinate seams between skin faces of one class."""
    fmap = occ.edge_face_map(env.solid)
    cls_of = {}
    for f, c in zip(env.skin_faces, env.classes):
        cls_of.setdefault(f.__hash__(), []).append((f, c))

    def cls(face):
        for g, c in cls_of.get(face.__hash__(), []):
            if g.IsSame(face):
                return c
        return None

    L = hull["x_fwd"] - hull["x_aft"]
    rows = []
    for i in range(1, fmap.Extent() + 1):
        e = occ.as_edge(fmap.FindKey(i))
        fs = [occ.as_face(f) for f in occ.list_of_shape(fmap.FindFromIndex(i))]
        if len(fs) != 2:
            continue
        c1, c2 = cls(fs[0]), cls(fs[1])
        if c1 != c2 or c1 not in ("deck", "bottom", "side"):
            continue
        P_ = occ.edge_points(e, 9)
        sd = P_.std(0)
        pm = P_[4]
        n1, n2 = occ.face_normal(fs[0], pm), occ.face_normal(fs[1], pm)
        ang = np.degrees(np.arccos(np.clip(abs(float(n1 @ n2)), -1, 1)))
        Lr = float(np.linalg.norm(np.diff(P_, axis=0), axis=1).sum())
        if c1 in ("deck", "bottom") and sd[1] < 0.3:
            rows.append((c1, float(P_[:, 1].mean()), Lr, ang, P_[:, 0].min(), P_[:, 0].max()))
        elif c1 == "side" and sd[2] < 0.3:
            rows.append((c1, float(P_[:, 2].mean()), Lr, ang, P_[:, 0].min(), P_[:, 0].max()))
    out = {"bottom": [], "deck": [], "side": [], "deck_cl": False, "keel_cl": False}
    for c in ("deck", "bottom", "side"):
        rs = [r for r in rows if r[0] == c]
        # centre line
        if c in ("deck", "bottom"):
            cl = [r for r in rs if abs(r[1]) < 0.5]
            if cl and sum(r[2] for r in cl) > 0.3 * L:
                out[c + "_cl" if c == "deck" else "keel_cl"] = True
            rs = [r for r in rs if abs(r[1]) >= 0.5]
        smooth = [r for r in rs if r[3] < 5.0]
        key = [abs(r[1]) for r in smooth]
        for grp in _cluster(key, 1.0):
            g = [smooth[i] for i in grp]
            v = float(np.mean([abs(r[1]) for r in g]))
            tot = sum(r[2] for r in g)
            if tot < 0.15 * L:
                continue
            if c != "side" and any(abs(v - gy) < 3.0 for gy in girders_abs):
                continue   # girder attachment line, not a stringer
            signs = set(np.sign(r[1]) for r in g)
            if c != "side" and len(signs) < 2:
                report["warnings"].append(f"{c} stringer |y|={v:.1f} 이 한쪽에만 있습니다 (대칭으로 생성).")
            out[c].append({"pos": round(v, 1), "x0": float(min(r[4] for r in g)),
                           "x1": float(max(r[5] for r in g)), "length": round(tot, 0)})
    # keel crease line (bottom faces meeting at y=0 with an angle)
    if not out["keel_cl"]:
        k = [r for r in rows if r[0] == "bottom" and abs(r[1]) < 0.5]
        out["keel_cl"] = bool(k) and sum(r[2] for r in k) > 0.3 * L
    return out


# ----------------------------------------------------------------------
def _table(xs, vs, nd=1):
    vs = [round(float(v), nd) for v in vs]
    if len(set(vs)) == 1:
        return vs[0]
    return {"x": [round(float(x), 1) for x in xs], "value": vs}


def _assemble(name, cad, mph, hull, bhs, frs, fcfg, girders, plates, seams, ov,
              report, sew_tol) -> dict:
    p = copy.deepcopy(P.DEFAULTS)
    defaults_used = []
    p["meta"] = {"name": name, "units": "mm", "source_cad": os.path.basename(cad),
                 "source_comsol": os.path.basename(mph) if mph else None,
                 "sew_tolerance": sew_tol}
    bb = hull["bbox"]
    p = {"meta": p["meta"],
         "hull": {"x_range": [round(bb[0], 1), round(bb[3], 1)],
                  "half_beam": round(hull["half_beam"], 1),
                  "z_keel": round(hull["z_keel"], 1), "z_deck": round(hull["z_deck"], 1),
                  "volume_m3": round(hull["volume_m3"], 3),
                  "skin_area_m2": round(hull["skin_area_m2"], 2)},
         **{k: v for k, v in p.items() if k not in ("meta",)}}
    ov = ov or {}
    # plating
    for k in p["plating"]:
        if ov.get("plating", {}).get(k) is not None:
            p["plating"][k] = ov["plating"][k]
        else:
            defaults_used.append(f"plating.{k}")
    if ov.get("material"):
        p["material"].update({k: v for k, v in ov["material"].items() if v is not None})
    else:
        defaults_used.append("material")

    # bulkheads
    bt = (ov.get("bulkheads") or {}).get("thickness") or []
    p["bulkheads"] = {
        "mode": "list", "positions": [s["x"] for s in bhs], "count": len(bhs),
        "thickness": bt[:len(bhs)] if len(bt) >= len(bhs) and bhs else 8.0,
        "stiffeners": {"vertical": [], "horizontal": []},
    }
    if not (len(bt) >= len(bhs) and bhs):
        defaults_used.append("bulkheads.thickness")
    bst = (ov.get("bulkheads") or {}).get("stiffeners") or []
    if bst:
        pats = [_line_pattern(b["lines"], b["sections"]) for b in bst[:len(bhs)]]
        if pats:
            p["bulkheads"]["stiffeners"] = pats[0]
            p["bulkheads"]["per_bulkhead"] = pats
    else:
        defaults_used.append("bulkheads.stiffeners (없음)")

    # frames
    fx = [s["x"] for s in frs]
    ft = (ov.get("frames") or {}).get("thickness") or []
    fr = {
        "mode": fcfg["mode"], "x_aft_ref": fcfg.get("x_aft_ref", 0.0),
        "x_fwd_ref": fcfg.get("x_fwd_ref"),
        "per_compartment": fcfg.get("per_compartment", []),
        "spacing": round(float(np.median(np.diff(fx))), 1) if len(fx) > 1 else 1000.0,
        "positions": fx, "min_gap_to_bulkhead": 150.0,
        "thickness": _table(fx, ft[:len(fx)], 2) if len(ft) >= len(fx) and fx else 8.0,
        "web_depth": _table(fx, [s["web_depth"] for s in frs]) if frs else 200.0,
        "deck_beam_depth": _table(fx, [s["deck_beam_depth"] for s in frs]) if frs else 100.0,
        "corner_radius": (ov.get("frames") or {}).get("corner_radius",
                                                      _table(fx, [s["corner_radius"] for s in frs]) if frs else 0.0),
        "flange": {"enabled": True, "section": P.DEFAULT_SECTION_FLAT},
    }
    if not (len(ft) >= len(fx) and fx):
        defaults_used.append("frames.thickness")
    fl = [s for s in (ov.get("frames") or {}).get("flange") or [] if s]
    if fl:
        fr["flange"]["section"] = fl[0] if all(s == fl[0] for s in fl) else fl[:len(fx)] \
            if len(fl) >= len(fx) else fl[0]
        # flange sizes vary only slightly -> use the mean (count-independent)
        fr["flange"]["section"] = {"shape": "FLAT",
                                   "height": round(float(np.mean([s["height"] for s in fl])), 1),
                                   "thickness": round(float(np.mean([s["thickness"] for s in fl])), 2)}
    else:
        defaults_used.append("frames.flange.section")
    p["frames"] = fr
    # ---- station table (one row per frame / bulkhead) - the main editable form
    rows = []
    ib = jf = 0
    rad_ov = (ov.get("frames") or {}).get("corner_radius")
    pats = []
    if bst:
        pats = [_line_pattern(b["lines"], b["sections"]) for b in bst]
    for st in sorted(bhs + frs, key=lambda s: s["x"]):
        if st["kind"] == "bulkhead":
            r = {"x": st["x"], "type": "bulkhead",
                 "thickness": bt[ib] if ib < len(bt) else None}
            if ib < len(pats):
                r["stiffeners"] = pats[ib]
            ib += 1
        else:
            r = {"x": st["x"], "type": "frame",
                 "thickness": round(ft[jf], 2) if jf < len(ft) else None,
                 "web_depth": st["web_depth"], "deck_beam_depth": st["deck_beam_depth"],
                 "corner_radius": rad_ov if rad_ov is not None else st["corner_radius"]}
            jf += 1
        rows.append({k: v for k, v in r.items() if v is not None})
    allx = [s["x"] for s in bhs + frs]
    p["stations"] = {"auto": {"first": min(allx) if allx else None,
                              "last": max(allx) if allx else None,
                              "spacing": fr["spacing"], "keep_bulkheads": True},
                     "list": rows}
    p["bulkheads"]["per_bulkhead"] = None     # patterns now live on the rows
    report["members"]["frames_fit"] = [
        {"name": s["name"], "x": s["x"], "web_depth": s["web_depth"],
         "deck_beam_depth": s["deck_beam_depth"], "corner_radius": s["corner_radius"],
         "iou": s["iou"]} for s in frs]

    # girders
    gov = ov.get("girders") or {}
    gl = []
    for i, g in enumerate(girders):
        inner = i == 0
        t = gov.get("thickness_inner" if inner else "thickness_outer")
        fls = gov.get("flange_inner" if inner else "flange_outer")
        if t is None:
            defaults_used.append(f"girders[{i}].thickness")
        gl.append({"name": f"Girder{i+1}", "y": g["y"], "symmetric": g["symmetric"],
                   "thickness": t if t is not None else 8.0,
                   "segments": g["segments"],
                   "flange": {"enabled": bool(fls), "section": fls or P.DEFAULT_SECTION_FLAT}})
    p["girders"] = gl
    report["members"]["girders_fit"] = [{"y": g["y"], "iou": g["iou"]} for g in girders]
    p["plates"] = [{"name": f"Plate{i+1}", "z": pl["z"], "x_from": pl["x_from"],
                    "x_to": pl["x_to"], "y_from": pl["y_from"], "y_to": pl["y_to"],
                    "thickness": 10.0} for i, pl in enumerate(plates)]
    if plates:
        defaults_used.append("plates.thickness")

    # stringers
    sov = ov.get("stringers") or {}
    stations = [{"name": s["name"], "x": s["x"]} for s in bhs + frs]
    for grp in ("bottom", "side", "deck"):
        items = sorted(seams[grp], key=lambda r: r["pos"])
        pos = [r["pos"] for r in items]
        secs = (sov.get(grp) or {}).get("sections") or []
        cfg = copy.deepcopy(P.DEFAULTS["stringers"][grp])
        cfg["mode"] = "list"
        cfg["positions"] = pos
        cfg["count"] = len(pos)
        if len(pos) > 1:
            cfg["start"] = pos[0]
            cfg["spacing"] = round(float(np.median(np.diff(pos))), 1)
        ext = [[_ref(r["x0"], stations, hull, "from"), _ref(r["x1"], stations, hull, "to")]
               for r in items]
        cfg["x_from"], cfg["x_to"] = "aft", "fwd"
        cfg["extents"] = ext if ext else None
        mp = (sov.get(grp) or {}).get("positions") or []
        if secs and mp:
            # match sections to positions by nearest COMSOL position
            m = []
            for v in pos:
                k = int(np.argmin([abs(v - q) for q in mp]))
                m.append(secs[k] if abs(mp[k] - v) < 5 and secs[k] else None)
            if all(m):
                cfg["section"] = m[0] if all(s == m[0] for s in m) else m
            else:
                defaults_used.append(f"stringers.{grp}.section")
        else:
            defaults_used.append(f"stringers.{grp}.section")
        p["stringers"][grp] = cfg

    # centre line
    cov = ov.get("centerline") or {}
    p["centerline"] = {
        "keel": {"enabled": bool(cov.get("keel")) or False,
                 "section": cov.get("keel") or P.DEFAULT_SECTION_FLAT},
        "deck": {"enabled": bool(cov.get("deck")) or bool(seams["deck_cl"]),
                 "section": cov.get("deck") or P.DEFAULT_SECTION_FLAT},
    }
    # transom
    tov = ov.get("transom") or {}
    if tov.get("lines"):
        zd = hull["z_deck"]
        lines = []
        for ln in tov["lines"]:
            lines.append({"o": ln["o"], "y1": abs(ln["y1"]) if ln["o"] == "V" else 0.0,
                          "y2": abs(ln["y2"]) if ln["o"] == "V" else max(abs(ln["y1"]), abs(ln["y2"])),
                          "z1": zd + ln["z1"], "z2": zd + ln["z2"]})
        p["transom"] = _line_pattern(lines, tov.get("sections") or [])
        report["warnings"].append("트랜섬 보강재 위치는 COMSOL 작업평면 좌표를 근사 변환한 "
                                  "값입니다 (transom 항목 확인 권장).")
    p["fixed_members"] = {"thickness": 8.0}
    report["defaults_used"] = defaults_used
    return p


def _line_pattern(lines, sections) -> dict:
    """COMSOL stiffener lines -> {vertical: [groups], horizontal: [...]}"""
    vert = defaultdict(list)
    hor = []
    for k, ln in enumerate(lines):
        sec = sections[k] if k < len(sections) and sections[k] else P.DEFAULT_SECTION_T
        if ln["o"] == "V":
            z0, z1 = sorted((ln["z1"], ln["z2"]))
            vert[(round(z0, -1), round(z1, -1))].append((abs(ln["y1"]), sec, z0, z1))
        else:
            hor.append({"z": round(ln["z1"], 1),
                        "y_max": round(max(abs(ln["y1"]), abs(ln["y2"])), 1),
                        "section": sec})
    groups = []
    for (_, _), items in sorted(vert.items(), key=lambda kv: -kv[0][1]):
        ys = sorted(set(round(i[0], 1) for i in items))
        g = {"mode": "list", "positions": ys,
             "z_from": round(min(i[2] for i in items), 1),
             "z_to": round(max(i[3] for i in items), 1),
             "symmetric": True, "section": items[0][1]}
        d = np.diff(ys)
        if len(ys) >= 3 and np.ptp(d) < 1.0:
            g.update({"mode": "spacing", "start": ys[0],
                      "spacing": round(float(np.mean(d)), 1), "count": len(ys)})
        groups.append(g)
    return {"vertical": groups, "horizontal": hor}
