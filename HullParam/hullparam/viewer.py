"""Tessellation of a model (or a bare envelope) into compact JSON for the
three.js viewer.  Arrays are little-endian base64 (Float32 / Uint32)."""
from __future__ import annotations

import base64
from typing import Dict, List

import numpy as np

from . import occ


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def _shell_mesh(faces, defl):
    vs, ts, off = [], [], 0
    for f in faces:
        v, t = occ.face_triangles(f)
        if len(t) == 0:
            continue
        vs.append(v)
        ts.append(t + off)
        off += len(v)
    if not vs:
        return None
    V = np.vstack(vs).astype(np.float32)
    T = np.vstack(ts).astype(np.uint32)
    return V, T


def _polylines(edges, defl):
    """Line segments (pairs of points) for a set of edges."""
    segs = []
    for e in edges:
        try:
            p = occ.edge_polyline(e, defl)
        except Exception:
            continue
        if len(p) >= 2:
            segs.append(np.repeat(p, 2, axis=0)[1:-1])
    if not segs:
        return None
    return np.vstack(segs).astype(np.float32)


def model_mesh(model, deflection: float = 3.0, issues=None) -> Dict:
    shells = [m for m in model.members if m.kind == "shell"]
    beams = [m for m in model.members if m.kind == "beam"]
    allf = occ.compound([f for m in shells for f in m.shapes])
    occ.mesh_shape(allf, deflection, 0.3)
    out: Dict = {"members": [], "bbox": occ.bbox(allf, optimal=False).round(1).tolist()}
    for m in shells:
        r = _shell_mesh(m.shapes, deflection)
        if r is None:
            continue
        V, T = r
        out["members"].append({
            "name": m.name, "kind": "shell", "category": m.category,
            "props": _props(m), "v": _b64(V), "t": _b64(T)})
    # outline edges of shells (unique), for the "edges" display
    edges = occ.unique([e for m in shells for f in m.shapes for e in occ.edges(f)])
    seg = _polylines(edges, deflection)
    if seg is not None:
        out["outline"] = _b64(seg)
    for m in beams:
        seg = _polylines(m.shapes, deflection)
        if seg is None:
            continue
        out["members"].append({"name": m.name, "kind": "beam", "category": m.category,
                               "props": _props(m), "l": _b64(seg)})
    if issues:
        out["issues"] = [i for i in issues if i.get("location")]
    return out


def envelope_mesh(env, deflection: float = 3.0) -> Dict:
    occ.mesh_shape(env.solid, deflection, 0.3)
    out = {"members": [], "bbox": occ.bbox(env.solid, optimal=False).round(1).tolist()}
    for cls in ("deck", "side", "bottom", "chine", "transom"):
        fs = env.faces_of(cls)
        r = _shell_mesh(fs, deflection)
        if r:
            out["members"].append({"name": f"Hull_{cls.capitalize()}", "kind": "shell",
                                   "category": "hull", "props": {"skin_class": cls},
                                   "v": _b64(r[0]), "t": _b64(r[1])})
    seg = _polylines(occ.unique([e for f in env.skin_faces for e in occ.edges(f)]), deflection)
    if seg is not None:
        out["outline"] = _b64(seg)
    return out


def _props(m) -> Dict:
    d = {}
    for k, v in m.props.items():
        if isinstance(v, float):
            d[k] = round(v, 3)
        else:
            d[k] = v
    if m.kind == "shell":
        d["area_m2"] = round(sum(occ.area(f) for f in m.shapes) / 1e6, 4)
        d["faces"] = len(m.shapes)
    else:
        d["length_m"] = round(sum(occ.length(e) for e in m.shapes) / 1e3, 4)
        d["edges"] = len(m.shapes)
    return d
