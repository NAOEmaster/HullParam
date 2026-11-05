"""Export of a generated model.

Files written to ``out_dir`` (prefix = model name):
  <name>_assembly.step   STEP AP214 assembly  MODEL / SHELLS / <member>,
                         MODEL / BEAMS / <member>  (names + colours)
  <name>_shells.step     all shell faces in ONE body (shared edges kept)
  <name>_beams.step      all beam edges (wireframe) in one body
  <name>_beams.igs       IGES of the beam lines   (imported as line bodies)
  <name>_shells.igs      optional (iges_shells=True); STEP is the reference
  <name>.brep            exact connected model (OpenCASCADE native)
  <name>_properties.csv  member table: type, thickness / section, area / length
  <name>_model.json      members, properties, face/edge indices into <name>.brep
  <name>_check.json      integrity check report
  <name>_params.yaml     parameters used
"""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, List

from OCP.IGESControl import IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_ColorCurv, XCAFDoc_DocumentTool

from . import occ
from . import params as P
from .occ import GeometryError

COLORS = {
    "hull": (0.78, 0.80, 0.84), "bulkhead": (0.85, 0.45, 0.30),
    "frame": (0.95, 0.70, 0.25), "girder": (0.35, 0.60, 0.85),
    "plate": (0.55, 0.75, 0.45), "fixed": (0.6, 0.6, 0.6),
    "beam": (0.80, 0.10, 0.10),
}


def _color(cat, kind):
    return COLORS["beam"] if kind == "beam" else COLORS.get(cat, (0.7, 0.7, 0.7))


def _name(lab, text):
    TDataStd_Name.Set_s(lab, TCollection_ExtendedString(text))


def write_step_assembly(model, path: str) -> None:
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetIVal_s("write.step.assembly", 1)
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    root = st.NewShape()
    _name(root, model.params.get("meta", {}).get("name", "MODEL"))
    for kind, title in (("shell", "SHELLS"), ("beam", "BEAMS")):
        ms = [m for m in model.members if m.kind == kind and m.shapes]
        if not ms:
            continue
        sub = st.NewShape()
        _name(sub, title)
        for m in ms:
            lab = st.AddShape(occ.compound(m.shapes), False)
            _name(lab, m.name)
            r, g, b = _color(m.category, m.kind)
            ct.SetColor(lab, Quantity_Color(r, g, b, Quantity_TOC_RGB),
                        XCAFDoc_ColorCurv if kind == "beam" else XCAFDoc_ColorSurf)
            comp = st.AddComponent(sub, lab, TopLoc_Location())
            _name(comp, m.name)
        c2 = st.AddComponent(root, sub, TopLoc_Location())
        _name(c2, title)
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.SetColorMode(True)
    if not w.Transfer(doc, STEPControl_AsIs):
        raise GeometryError("STEP 어셈블리 변환 실패")
    if int(w.Write(path)) != 1:   # IFSelect_RetDone
        raise GeometryError(f"STEP 저장 실패: {path}")


def write_step(shape, path: str) -> None:
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    if int(w.Write(path)) != 1:
        raise GeometryError(f"STEP 저장 실패: {path}")


def write_iges(shape, path: str) -> None:
    Interface_Static.SetCVal_s("write.iges.unit", "MM")
    Interface_Static.SetIVal_s("write.iges.brep.mode", 1)
    w = IGESControl_Writer("MM", 1)
    w.AddShape(shape)
    w.ComputeModel()
    if not w.Write(path):
        raise GeometryError(f"IGES 저장 실패: {path}")


def member_rows(model) -> List[Dict]:
    rows = []
    for m in model.members:
        r = {"name": m.name, "type": m.kind, "category": m.category,
             "count": len(m.shapes)}
        if m.kind == "shell":
            r["thickness_mm"] = m.props.get("thickness")
            r["area_m2"] = round(sum(occ.area(f) for f in m.shapes) / 1e6, 5)
        else:
            sec = m.props.get("section") or {}
            r["section"] = sec.get("shape")
            r.update({f"sec_{k}": v for k, v in sec.items() if k != "shape"})
            sp = P.section_props(sec) if sec else {}
            r["sec_A_mm2"] = round(sp.get("A", 0.0), 3)
            r["sec_Iyy_mm4"] = round(sp.get("Iyy", 0.0), 1)
            r["sec_Izz_mm4"] = round(sp.get("Izz", 0.0), 1)
            r["length_m"] = round(sum(occ.length(e) for e in m.shapes) / 1e3, 5)
        for k in ("x", "y", "z", "host", "web_depth", "deck_beam_depth", "corner_radius",
                  "skin_class"):
            if k in m.props:
                r[k] = m.props[k]
        rows.append(r)
    return rows


def export_all(model, out_dir: str, check_report=None, log=print,
               formats=("step", "iges", "brep"), iges_shells: bool = False) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    name = model.params.get("meta", {}).get("name", "model")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    pre = os.path.join(out_dir, safe)
    files = []
    shells = [f for m in model.members if m.kind == "shell" for f in m.shapes]
    beams = [e for m in model.members if m.kind == "beam" for e in m.shapes]
    shell_c = occ.compound(occ.unique(shells))
    beam_c = occ.compound(occ.unique(beams))
    if "step" in formats:
        write_step_assembly(model, pre + "_assembly.step")
        write_step(shell_c, pre + "_shells.step")
        files += [pre + "_assembly.step", pre + "_shells.step"]
        if beams:
            write_step(beam_c, pre + "_beams.step")
            files.append(pre + "_beams.step")
    if "iges" in formats:
        if iges_shells:   # IGES trimmed B-spline faces may lose precision -> STEP preferred
            write_iges(shell_c, pre + "_shells.igs")
            files.append(pre + "_shells.igs")
        if beams:
            write_iges(beam_c, pre + "_beams.igs")
            files.append(pre + "_beams.igs")
    # exact connected model + index map
    full = occ.compound([shell_c, beam_c])
    occ.write_brep(full, pre + ".brep")
    files.append(pre + ".brep")
    rb = occ.read_brep(pre + ".brep")
    f_all, e_all = occ.faces(rb), occ.edges(rb)

    def index_map(shapes):
        b = {}
        for k, s in enumerate(shapes):
            b.setdefault(_geo_key(s), []).append(k)
        return b
    fkey, ekey = index_map(f_all), index_map(e_all)
    mj = {"name": name, "units": "mm", "brep": os.path.basename(pre + ".brep"),
          "material": model.params.get("material"), "members": []}
    for m, row in zip(model.members, member_rows(model)):
        keys = fkey if m.kind == "shell" else ekey
        idx = sorted(set(i for s in m.shapes for i in keys.get(_geo_key(s), [])[:1]))
        mj["members"].append({**row, "props": _clean(m.props),
                              ("face_ids" if m.kind == "shell" else "edge_ids"): idx})
    with open(pre + "_model.json", "w", encoding="utf-8") as fh:
        json.dump(mj, fh, indent=1, ensure_ascii=False)
    files.append(pre + "_model.json")
    rows = member_rows(model)
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(pre + "_properties.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    files.append(pre + "_properties.csv")
    if check_report is not None:
        with open(pre + "_check.json", "w", encoding="utf-8") as fh:
            json.dump(check_report, fh, indent=1, ensure_ascii=False)
        files.append(pre + "_check.json")
    P.save(model.params, pre + "_params.yaml")
    files.append(pre + "_params.yaml")
    log("내보내기 완료:\n  " + "\n  ".join(os.path.basename(f) for f in files))
    return files


def _geo_key(s):
    """Geometry key robust to re-reading (centroid + size)."""
    if s.ShapeType() == occ.KIND["face"]:
        c = occ.centroid(s)
        return (round(c[0], 2), round(c[1], 2), round(c[2], 2), round(occ.area(s), 1))
    c = occ.centroid(s, 1)
    return (round(c[0], 2), round(c[1], 2), round(c[2], 2), round(occ.length(s), 2))


def _clean(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = round(v, 4)
        else:
            out[k] = v
    return out
