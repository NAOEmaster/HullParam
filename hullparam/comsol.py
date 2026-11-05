"""Read parameters from a COMSOL .mph file without COMSOL.

A .mph file is a zip archive; ``dmodel.xml`` holds the model tree including the
global parameter table and the full command history.  Only plain numeric
parameters are needed here (thickness, beam sections, stiffener lines).

The mapping in ``gpph_overrides`` follows the naming used in the GPPH COMSOL
model (tDeck, tF1.., SS_H1.., B1_3_y1V, ...).  Names that do not exist are
simply ignored, so the import is harmless for other models.
"""
from __future__ import annotations

import html
import re
import zipfile
from typing import Dict, List

_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(\[[^\]]*\])?\s*$")
_UNIT = {"[mm]": 1.0, "[m]": 1000.0, "[cm]": 10.0, None: 1.0}


def read_parameters(path: str) -> Dict[str, float]:
    """Numeric global parameters of a .mph file (lengths in mm as stored)."""
    with zipfile.ZipFile(path) as z:
        if "dmodel.xml" not in z.namelist():
            raise ValueError("dmodel.xml 이 없는 .mph 파일입니다.")
        xml = z.read("dmodel.xml").decode("utf-8", "replace")
    out: Dict[str, float] = {}
    for name, expr in re.findall(
            r'<expressions T="\d+" name="([^"]*)" expr="([^"]*)"', xml):
        m = _NUM.match(html.unescape(expr))
        if not m:
            continue
        val = float(m.group(1))
        unit = m.group(2)
        if unit in _UNIT:
            val *= _UNIT[unit]
        elif unit is not None:
            # non-length unit (Pa, kg ...) - keep raw value
            pass
        out[name] = val
    # material (first material only)
    mats = {}
    for key, pat in (("E", r's\("E"\) s\("([^"]+)"\)'),
                     ("nu", r's\("nu"\) s\("([^"]+)"\)'),
                     ("density", r's\("density"\) s\("([^"]+)"\)'),
                     ("sigmat", r's\("sigmat"\) s\("([^"]+)"\)')):
        m = re.search(pat, html.unescape(xml))
        if m:
            mats[key] = m.group(1)
    lab = re.search(r'm\(s\("label"\)\) s\("(Aluminum[^"]*|Steel[^"]*|[^"]*'
                    r'[Aa]lloy[^"]*)"\)', html.unescape(xml))
    if lab:
        mats["name"] = lab.group(1)
    out["__material__"] = mats  # type: ignore
    return out


def _get(p, name, default=None):
    v = p.get(name)
    return v if isinstance(v, (int, float)) else default


def _seq(p, fmt, start=1):
    vals = []
    i = start
    while True:
        v = _get(p, fmt.format(i))
        if v is None:
            break
        vals.append(v)
        i += 1
    return vals


def t_section(p, prefix, k):
    """COMSOL T profile: height = H+h, width = B, t_web = h, t_flange = b."""
    H, h = _get(p, f"{prefix}_H{k}"), _get(p, f"{prefix}_h{k}")
    B, b = _get(p, f"{prefix}_B{k}"), _get(p, f"{prefix}_b{k}")
    if None in (H, h, B, b):
        return None
    return {"shape": "T", "height": round(H + h, 3), "width": round(B, 3),
            "t_web": round(h, 3), "t_flange": round(b, 3)}


def rect_section(h, w):
    """COMSOL rectangular beam -> flat bar (long side = height)."""
    if h is None or w is None:
        return None
    return {"shape": "FLAT", "height": round(max(h, w), 3), "thickness": round(min(h, w), 3)}


def _parse_unit_value(s, scale):
    m = re.match(r"\s*([-+0-9.eE]+)\s*(\[[^\]]*\])?", s or "")
    if not m:
        return None
    v = float(m.group(1))
    u = m.group(2) or ""
    mult = {"[Pa]": 1e-6, "[MPa]": 1.0, "[GPa]": 1e3, "[kg/m^3]": 1.0}.get(u, 1.0)
    return v * mult * scale


def lines(p, prefix) -> List[dict]:
    """Stiffener lines like B1_3_y1V ... -> [{'o':'V','y1','z1','y2','z2'}]."""
    out = []
    k = 1
    while True:
        found = None
        for o in ("V", "H"):
            vals = [_get(p, f"{prefix}_{k}_{c}{o}") for c in ("y1", "z1", "y2", "z2")]
            if None not in vals:
                found = dict(zip(("y1", "z1", "y2", "z2"), vals), o=o)
        if not found:
            break
        out.append(found)
        k += 1
    return out


def gpph_overrides(p: Dict[str, float]) -> dict:
    """Thickness / sections / stiffener data found in a GPPH-style model."""
    o: dict = {"plating": {}, "frames": {}, "bulkheads": {}, "girders": {},
               "stringers": {}, "centerline": {}, "transom": {}, "material": {}}
    for cls, name in (("deck", "tDeck"), ("side", "tSide"), ("chine", "tStrip"),
                      ("bottom", "tBottom"), ("transom", "tStern")):
        v = _get(p, name)
        if v is not None:
            o["plating"][cls] = round(v, 3)
    o["frames"]["thickness"] = [round(v, 3) for v in _seq(p, "tF{}")]
    fl = []
    k = 1
    while _get(p, f"FS_h{k}") is not None:
        fl.append(rect_section(_get(p, f"FS_h{k}"), _get(p, f"FS_B{k}")))
        k += 1
    o["frames"]["flange"] = fl
    r = _get(p, "F_FilletRadius")
    if r is not None:
        o["frames"]["corner_radius"] = round(r, 1)
    o["bulkheads"]["thickness"] = [round(v, 3) for v in _seq(p, "tBH{}")]
    bh_st = []
    i = 1
    while lines(p, f"B{i}"):
        ls = lines(p, f"B{i}")
        secs = [t_section(p, f"BH{i}S", k) for k in range(1, len(ls) + 1)]
        bh_st.append({"lines": ls, "sections": secs})
        i += 1
    o["bulkheads"]["stiffeners"] = bh_st
    gi, ge = _get(p, "tGirdersInner"), _get(p, "tGirdersOuter")
    o["girders"]["thickness_inner"] = round(gi, 3) if gi else None
    o["girders"]["thickness_outer"] = round(ge, 3) if ge else None
    o["girders"]["flange_inner"] = rect_section(_get(p, "GSin_h"), _get(p, "GSin_B"))
    o["girders"]["flange_outer"] = rect_section(_get(p, "GSout_h"), _get(p, "GSout_B"))
    for grp, pre, pos in (("side", "SS", "zSideStringer_{}"),
                          ("deck", "DS", "yDeckStringer_{}"),
                          ("bottom", "BS", "yBottStringer_{}")):
        ps = _seq(p, pos)
        secs = [t_section(p, pre, k) for k in range(1, len(ps) + 1)]
        o["stringers"][grp] = {"positions": [round(v, 1) for v in ps],
                               "sections": secs}
    o["centerline"]["keel"] = rect_section(_get(p, "BmS_b"), _get(p, "BmS_H"))
    o["centerline"]["deck"] = rect_section(_get(p, "DmS_b"), _get(p, "DmS_H"))
    st = lines(p, "St")
    o["transom"]["lines"] = st
    o["transom"]["sections"] = [t_section(p, "StS", k) for k in range(1, len(st) + 1)]
    mat = p.get("__material__") or {}
    if mat:
        o["material"] = {
            "name": mat.get("name", "from COMSOL"),
            "E_MPa": _parse_unit_value(mat.get("E"), 1.0),
            "nu": _parse_unit_value(mat.get("nu"), 1.0),
            "density_kg_m3": _parse_unit_value(mat.get("density"), 1.0),
        }
    return o
