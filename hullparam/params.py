"""Parameter file (YAML) schema, validation and expansion into a concrete
layout of members.

Conventions
-----------
* Units: millimetres.  x forward, y to port (+) / starboard (-), z up.
* Symmetric longitudinal members are given by |y| (one entry per side pair).
* Values that vary along the ship can be a number or a table
  ``{x: [...], value: [...]}`` (linear interpolation, constant outside).
* x references usable in girder/plate/stringer limits:
  ``aft``, ``fwd``, ``BH1``..``BHn`` (bulkheads aft->fwd), ``F1``..``Fn``
  (frames aft->fwd) or a number.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

SECTION_SHAPES = ("T", "L", "FLAT")

DEFAULT_SECTION_T = {"shape": "T", "height": 60.0, "width": 50.0,
                     "t_web": 6.0, "t_flange": 6.0}
DEFAULT_SECTION_FLAT = {"shape": "FLAT", "height": 120.0, "thickness": 12.0}

DEFAULTS: Dict[str, Any] = {
    "meta": {"name": "model", "units": "mm"},
    "build": {
        "fuzzy_tolerance": 0.01,      # mm, boolean fuzzy value
        "snap_tolerance": 2.0,        # mm, snap members onto existing skin seams
        "min_station_gap": 100.0,     # mm, min distance between frames/BHs
        "min_member_gap": 30.0,       # mm, min distance between parallel members
        "check_small_edge": 2.0,      # mm, report edges shorter than this
        "check_small_face": 200.0,    # mm2, report faces smaller than this
        "viewer_deflection": 3.0,     # mm, tessellation for the 3D viewer
    },
    "material": {"name": "Aluminium", "E_MPa": 70000.0, "nu": 0.33,
                 "density_kg_m3": 2700.0},
    "plating": {"deck": 5.0, "side": 5.0, "bottom": 6.0, "chine": 6.0,
                "transom": 6.0},
    "bulkheads": {
        "mode": "list", "positions": [], "count": 0, "thickness": 8.0,
        "stiffeners": {"vertical": [], "horizontal": []},
    },
    "frames": {
        "mode": "per_compartment", "x_aft_ref": 0.0, "x_fwd_ref": None,
        "per_compartment": [], "spacing": 1000.0, "positions": [],
        "min_gap_to_bulkhead": 150.0,
        "thickness": 8.0, "web_depth": 200.0, "deck_beam_depth": 100.0,
        "corner_radius": 0.0,
        "flange": {"enabled": True, "section": DEFAULT_SECTION_FLAT},
    },
    "stations": {"auto": {"first": None, "last": None, "spacing": 1000.0,
                          "keep_bulkheads": True},
                 "list": []},
    "girders": [],
    "plates": [],
    "stringers": {
        "bottom": {"mode": "list", "positions": [], "start": 200.0,
                   "spacing": 200.0, "count": 0, "skip_near_girders": 50.0,
                   "x_from": "aft", "x_to": "fwd", "extents": None,
                   "section": DEFAULT_SECTION_T},
        "side": {"mode": "list", "positions": [], "start": 700.0,
                 "spacing": 400.0, "count": 0, "x_from": "aft", "x_to": "fwd",
                 "extents": None, "section": DEFAULT_SECTION_T},
        "deck": {"mode": "list", "positions": [], "start": 200.0,
                 "spacing": 500.0, "count": 0, "skip_near_girders": 0.0,
                 "x_from": "aft", "x_to": "fwd", "extents": None,
                 "section": DEFAULT_SECTION_T},
    },
    "centerline": {
        "keel": {"enabled": False, "section": DEFAULT_SECTION_FLAT},
        "deck": {"enabled": False, "section": DEFAULT_SECTION_FLAT},
    },
    "transom": {"vertical": [], "horizontal": []},
    "fixed_members": {"thickness": 8.0},
}


class ParamError(ValueError):
    pass


# ----------------------------------------------------------------------
# IO
# ----------------------------------------------------------------------
def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return deep_merge(DEFAULTS, data)


def loads(text: str) -> dict:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ParamError("파라미터 파일 최상위는 사전(key: value) 형식이어야 합니다.")
    return deep_merge(DEFAULTS, data)


_HEADER = """\
# =====================================================================
#  HullParam parameter file  ({name})
#  단위 mm / 좌표: x 선수방향(+), y 좌현(+), z 위(+)
#  - 대칭 부재(거더, stringer)는 |y| 로 한쪽만 입력합니다.
#  - 값이 위치에 따라 변하면 표로 입력:  {{x: [..], value: [..]}} (선형보간)
#  - x 참조:  aft, fwd, BH1..BHn(격벽, 선미→선수), F1..Fn(늑골) 또는 숫자
#  - 생성 후 무결성 검사 결과는 report 에 기록됩니다.
#  생성: {date}
# =====================================================================
"""

_COMMENTS = {
    "meta": "모델 정보",
    "hull": "선체 외판 정보 (자동 추출, 참고용 - 수정해도 반영되지 않음)",
    "build": "생성/검사 공차 설정",
    "material": "재료 (Nastran 출력 등에 사용)",
    "plating": "외판 두께 [mm] - deck/side/bottom/chine(스프레이 스트립)/transom",
    "bulkheads": "격벽: mode=list(위치 직접) | equal(개수로 균등 배치)",
    "frames": "늑골 공통값(표에서 비워 둔 항목에 사용) / stations 표가 없을 때의 배치 방식",
    "stations": "횡부재 표: 위치별 종류(frame|bulkhead)와 치수. auto = 첫 위치~마지막 위치를 목표 간격으로 자동 배치",
    "girders": "종방향 거더 (y 평면): 구간별 x 범위와 상단 높이 z_top",
    "plates": "수평판 (z 평면, 엔진 받침판 등)",
    "stringers": "종보강재(beam): bottom/deck 는 |y|, side 는 z 위치. mode=list | spacing",
    "centerline": "중심선 보강재(beam): keel(선저 중심선), deck(갑판 중심선)",
    "transom": "트랜섬 보강재(beam): vertical(|y| 위치) / horizontal(z 위치)",
    "fixed_members": "자동 인식되지 않은 부재(그대로 유지)의 두께",
}


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def dumps(p: dict) -> str:
    p = copy.deepcopy(p)
    name = (p.get("meta") or {}).get("name", "model")
    out = [_HEADER.format(name=name, date=_dt.datetime.now().strftime(
        "%Y-%m-%d %H:%M"))]
    for k, v in p.items():
        if k in _COMMENTS:
            out.append(f"\n# --- {_COMMENTS[k]}")
        out.append(yaml.dump({k: v}, Dumper=_NoAliasDumper, sort_keys=False,
                             allow_unicode=True, default_flow_style=None,
                             width=100).rstrip())
    return "\n".join(out) + "\n"


def save(p: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(p))


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def interp(val, x: float, what: str = "값") -> float:
    """Number or {x:[..], value:[..]} table -> float at x."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict) and "x" in val and "value" in val:
        xs, vs = list(map(float, val["x"])), list(map(float, val["value"]))
        if len(xs) != len(vs) or not xs:
            raise ParamError(f"{what}: 표의 x 와 value 길이가 다릅니다.")
        pairs = sorted(zip(xs, vs))
        xs, vs = [a for a, _ in pairs], [b for _, b in pairs]
        if x <= xs[0]:
            return vs[0]
        if x >= xs[-1]:
            return vs[-1]
        for i in range(1, len(xs)):
            if x <= xs[i]:
                t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
                return vs[i - 1] + t * (vs[i] - vs[i - 1])
    if isinstance(val, list) and val and all(isinstance(v, (int, float)) for v in val):
        raise ParamError(f"{what}: 목록 대신 숫자 하나 또는 x/value 표를 사용하세요.")
    raise ParamError(f"{what}: 숫자 또는 {{x: [...], value: [...]}} 형식이어야 합니다 ({val!r}).")


_PICK_WARN: List[str] = []


def pick(val, i: int, n: int, what: str):
    """Scalar, or list with one entry per item (len n).  If the list length
    differs from the number of items (e.g. the count was changed) the last
    entry is reused for extra items and a warning is recorded."""
    if isinstance(val, list):
        if len(val) == 0:
            raise ParamError(f"{what}: 빈 목록입니다.")
        if len(val) != n:
            msg = (f"{what}: 목록 길이 {len(val)} ≠ 부재 수 {n} → "
                   f"부족한 항목은 마지막 값 사용")
            if msg not in _PICK_WARN:
                _PICK_WARN.append(msg)
            return val[min(i, len(val) - 1)]
        return val[i]
    return val


SECTION_LABELS = {"T": "T형 (T-bar)", "L": "앵글 (L / angle)", "FLAT": "평강 (flat bar)"}


def check_section(sec, what: str) -> dict:
    """Validate a beam section and return it in canonical form.

    T / L : height (전체 높이), width (플랜지 폭), t_web, t_flange
    FLAT  : height (판 폭 = 높이), thickness
    RECT  : legacy  {height, width}  -> FLAT (long side = height)
    """
    if not isinstance(sec, dict):
        raise ParamError(f"{what}: 단면은 {{shape: T|L|FLAT, ...}} 형식이어야 합니다.")
    shape = str(sec.get("shape", "")).upper()
    if shape in ("ANGLE",):
        shape = "L"
    if shape == "RECT" or (shape == "FLAT" and "thickness" not in sec and "width" in sec):
        h, w = sec.get("height"), sec.get("width")
        for k, v in (("height", h), ("width", w)):
            if not isinstance(v, (int, float)) or v <= 0:
                raise ParamError(f"{what}: 단면 {k} 는 0보다 큰 숫자여야 합니다 ({v!r}).")
        return {"shape": "FLAT", "height": float(max(h, w)), "thickness": float(min(h, w))}
    if shape not in ("T", "L", "FLAT"):
        raise ParamError(f"{what}: 단면 종류는 T, L(앵글), FLAT(평강) 중 하나입니다 ({sec.get('shape')}).")
    need = ("height", "thickness") if shape == "FLAT" else ("height", "width", "t_web", "t_flange")
    for k in need:
        v = sec.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ParamError(f"{what}: 단면 {k} 는 0보다 큰 숫자여야 합니다 ({v!r}).")
    out = {"shape": shape}
    out.update({k: float(sec[k]) for k in need})
    if shape in ("T", "L") and out["t_flange"] >= out["height"]:
        raise ParamError(f"{what}: 플랜지 두께가 전체 높이보다 큽니다.")
    return out


def section_props(sec: dict) -> dict:
    """Area, centroid and second moments about the section's own centroid.
    y = across the web (parallel to the plating), z = along the web
    (normal to the plating, origin at the plating)."""
    sec = check_section(sec, "section")
    s = sec["shape"]
    if s == "FLAT":
        h, t = sec["height"], sec["thickness"]
        return {"A": h * t, "Iyy": t * h ** 3 / 12, "Izz": h * t ** 3 / 12,
                "z_c": h / 2, "y_c": 0.0}
    h, b, tw, tf = sec["height"], sec["width"], sec["t_web"], sec["t_flange"]
    hw = h - tf
    Aw, Af = hw * tw, b * tf
    A = Aw + Af
    zw, zf = hw / 2, hw + tf / 2
    zc = (Aw * zw + Af * zf) / A
    Iyy = tw * hw ** 3 / 12 + Aw * (zw - zc) ** 2 + b * tf ** 3 / 12 + Af * (zf - zc) ** 2
    if s == "T":
        yw = yf = 0.0
    else:          # angle: web at y in [0, tw], flange from y=0 to y=b
        yw, yf = tw / 2, b / 2
    yc = (Aw * yw + Af * yf) / A
    Izz = hw * tw ** 3 / 12 + Aw * (yw - yc) ** 2 + tf * b ** 3 / 12 + Af * (yf - yc) ** 2
    Iyz = Aw * (yw - yc) * (zw - zc) + Af * (yf - yc) * (zf - zc)
    out = {"A": A, "Iyy": Iyy, "Izz": Izz, "z_c": zc, "y_c": yc}
    if s == "L":
        out["Iyz"] = Iyz
    return out


# ----------------------------------------------------------------------
# expanded layout
# ----------------------------------------------------------------------
@dataclass
class Station:
    name: str
    kind: str            # 'bulkhead' | 'frame'
    x: float
    thickness: float
    web_depth: float = 0.0
    deck_beam_depth: float = 0.0
    corner_radius: float = 0.0
    flange: Optional[dict] = None
    index: int = 0       # 1-based within kind
    stiffeners: Optional[dict] = None   # bulkhead row override


@dataclass
class Girder:
    name: str
    y: float
    thickness: float
    segments: List[Tuple]   # (x0, x1, z_top number|table, ref0, ref1)
    flange: Optional[dict]


@dataclass
class Plate:
    name: str
    z: float
    x0: float
    x1: float
    y0: float
    y1: float
    thickness: float
    x0_ref: Optional[Tuple[str, float]] = None
    x1_ref: Optional[Tuple[str, float]] = None


@dataclass
class BeamLine:
    name: str
    group: str           # stringer_bottom, stringer_side, ...
    host: str            # skin class / 'bulkhead' / 'transom'
    axis: str            # plane normal used to cut: 'x'|'y'|'z'
    value: float         # plane position
    x0: float = -1e9
    x1: float = 1e9
    z0: float = -1e9
    z1: float = 1e9
    y_abs_max: float = 1e9
    section: Optional[dict] = None
    station: Optional[str] = None   # host station name for bulkhead lines
    x0_ref: Optional[Tuple[str, float]] = None
    x1_ref: Optional[Tuple[str, float]] = None


@dataclass
class Layout:
    stations: List[Station] = field(default_factory=list)
    girders: List[Girder] = field(default_factory=list)
    plates: List[Plate] = field(default_factory=list)
    beams: List[BeamLine] = field(default_factory=list)
    plating: Dict[str, float] = field(default_factory=dict)
    centerline: Dict[str, Optional[dict]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def station_x(self) -> Dict[str, float]:
        d = {"aft": -1e9, "fwd": 1e9}
        for s in self.stations:
            d[("BH" if s.kind == "bulkhead" else "F") + str(s.index)] = s.x
        return d


def _num(v, what):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise ParamError(f"{what}: 숫자를 입력하세요 ({v!r}).")
    return float(v)


def _pos_list(v, what) -> List[float]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ParamError(f"{what}: 숫자 목록이어야 합니다.")
    return [_num(a, what) for a in v]


def _series(cfg: dict, what: str, girders_abs: List[float] = (),
            skip_key: str = "skip_near_girders") -> List[float]:
    mode = cfg.get("mode", "list")
    if mode == "list":
        vals = _pos_list(cfg.get("positions"), f"{what}.positions")
    elif mode == "auto":
        # from the centre line: spacing, 2*spacing, ... (count per side)
        sp = _num(cfg.get("spacing"), f"{what}.spacing")
        n = cfg.get("count")
        if not isinstance(n, int) or n < 0:
            raise ParamError(f"{what}.count: 0 이상의 정수를 입력하세요.")
        if sp <= 0 and n > 0:
            raise ParamError(f"{what}.spacing: 0보다 커야 합니다.")
        vals = [sp * (i + 1) for i in range(n)]
        skip = float(cfg.get(skip_key) or 0.0)
        if skip > 0 and girders_abs:
            vals = [v for v in vals if all(abs(v - g) > skip for g in girders_abs)]
    elif mode == "spacing":
        start = _num(cfg.get("start"), f"{what}.start")
        sp = _num(cfg.get("spacing"), f"{what}.spacing")
        n = cfg.get("count")
        if not isinstance(n, int) or n < 0:
            raise ParamError(f"{what}.count: 0 이상의 정수를 입력하세요.")
        if sp <= 0 and n > 1:
            raise ParamError(f"{what}.spacing: 0보다 커야 합니다.")
        vals = [start + i * sp for i in range(n)]
        skip = float(cfg.get(skip_key) or 0.0)
        if skip > 0 and girders_abs:
            vals = [v for v in vals if all(abs(v - g) > skip for g in girders_abs)]
    else:
        raise ParamError(f"{what}.mode: auto, spacing 또는 list ({mode!r}).")
    return sorted(vals)


def ref_of(ref) -> Optional[Tuple[str, float]]:
    """'BH1' / 'F3+20' -> ('BH1', 0.0) / ('F3', 20.0); other -> None."""
    if isinstance(ref, str):
        m = re.fullmatch(r"(BH|F)(\d+)\s*([+-]\s*\d+(?:\.\d+)?)?", ref.strip())
        if m:
            off = float(m.group(3).replace(" ", "")) if m.group(3) else 0.0
            return (m.group(1) + m.group(2), off)
    return None


def resolve_x(ref, refs: Dict[str, float], what: str) -> float:
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        return float(ref)
    if isinstance(ref, str):
        key = ref.strip()
        if key in refs:
            return refs[key]
        m = re.fullmatch(r"(BH|F)(\d+)\s*([+-]\s*\d+(?:\.\d+)?)?", key)
        if m and (m.group(1) + m.group(2)) in refs:
            off = float(m.group(3).replace(" ", "")) if m.group(3) else 0.0
            return refs[m.group(1) + m.group(2)] + off
        raise ParamError(f"{what}: 참조 '{ref}' 를 찾을 수 없습니다 "
                         f"(가능: {', '.join(sorted(refs))}).")
    raise ParamError(f"{what}: x 참조는 숫자 또는 aft/fwd/BHn/Fn 입니다 ({ref!r}).")


def expand(p: dict, hull: dict) -> Layout:
    """Turn the parameter dict into concrete members.  ``hull`` is the envelope
    info dict (bbox, z_deck, ...).  Raises ParamError with a Korean message."""
    L = Layout()
    _PICK_WARN.clear()
    try:
        _expand(p, hull, L)
    finally:
        L.warnings.extend(_PICK_WARN)
    return L


def _expand(p: dict, hull: dict, L: Layout) -> None:
    xa, xf = hull["x_aft"], hull["x_fwd"]
    bmin = p["build"]
    gap = float(bmin.get("min_station_gap", 100.0))

    # ---------------- plating
    for cls in ("deck", "side", "bottom", "chine", "transom"):
        L.plating[cls] = _num(p["plating"].get(cls), f"plating.{cls}")
        if L.plating[cls] <= 0:
            raise ParamError(f"plating.{cls}: 두께는 0보다 커야 합니다.")

    bh = p["bulkheads"]
    fr = p["frames"]
    tab = (p.get("stations") or {}).get("list") or []
    if tab:
        _table_stations(p, hull, L, gap)
    else:
        # ---------------- bulkheads / frames (legacy blocks)
        x_aft_ref = _num(fr.get("x_aft_ref", 0.0), "frames.x_aft_ref")
        x_fwd_ref = fr.get("x_fwd_ref")
        x_fwd_ref = _num(x_fwd_ref, "frames.x_fwd_ref") if x_fwd_ref is not None else xf
        if not x_aft_ref < x_fwd_ref:
            raise ParamError("frames.x_aft_ref < frames.x_fwd_ref 이어야 합니다.")
        if bh.get("mode", "list") == "list":
            bxs = sorted(_pos_list(bh.get("positions"), "bulkheads.positions"))
        elif bh["mode"] == "equal":
            n = bh.get("count", 0)
            if not isinstance(n, int) or n < 0:
                raise ParamError("bulkheads.count: 0 이상의 정수")
            bxs = [x_aft_ref + (x_fwd_ref - x_aft_ref) * (i + 1) / (n + 1) for i in range(n)]
        else:
            raise ParamError("bulkheads.mode: list 또는 equal")
        for i, x in enumerate(bxs):
            if not (xa + gap / 2 < x < xf - gap / 2):
                raise ParamError(f"격벽 BH{i+1} x={x:.1f} 이(가) 선체 범위 "
                                 f"({xa:.0f} ~ {xf:.0f}) 밖입니다.")
            if i and x - bxs[i - 1] < gap:
                raise ParamError(f"격벽 BH{i} 와 BH{i+1} 간격({x - bxs[i-1]:.0f} mm)이 "
                                 f"최소 간격 {gap:.0f} mm 보다 작습니다.")
        for i, x in enumerate(bxs):
            t = _num(pick(bh.get("thickness"), i, len(bxs), "bulkheads.thickness"),
                     "bulkheads.thickness")
            if t <= 0:
                raise ParamError("bulkheads.thickness 는 0보다 커야 합니다.")
            L.stations.append(Station(f"BH{i+1}", "bulkhead", x, t, index=i + 1))

        # ---------------- frames
        mode = fr.get("mode", "per_compartment")
        if mode == "per_compartment":
            counts = fr.get("per_compartment") or []
            if not isinstance(counts, list) or not all(isinstance(c, int) and c >= 0 for c in counts):
                raise ParamError("frames.per_compartment: 0 이상 정수 목록 (구획 수 = 격벽 수 + 1)")
            if len(counts) != len(bxs) + 1:
                raise ParamError(f"frames.per_compartment 항목 수({len(counts)})가 구획 수"
                                 f"({len(bxs) + 1} = 격벽 {len(bxs)}개 + 1)와 다릅니다.")
            bounds = [x_aft_ref] + bxs + [x_fwd_ref]
            fxs = []
            for k, n in enumerate(counts):
                a, b = bounds[k], bounds[k + 1]
                fxs += [a + (b - a) * (j + 1) / (n + 1) for j in range(n)]
        elif mode == "spacing":
            sp = _num(fr.get("spacing"), "frames.spacing")
            if sp < gap:
                raise ParamError(f"frames.spacing 은 최소 간격 {gap:.0f} mm 이상이어야 합니다.")
            mg = float(fr.get("min_gap_to_bulkhead", gap))
            fxs, x = [], x_aft_ref + sp
            while x < x_fwd_ref - 1e-6:
                if all(abs(x - b) >= mg for b in bxs):
                    fxs.append(x)
                x += sp
        elif mode == "list":
            fxs = sorted(_pos_list(fr.get("positions"), "frames.positions"))
        else:
            raise ParamError("frames.mode: per_compartment | spacing | list")
        allx = sorted([(x, "B") for x in bxs] + [(x, "F") for x in fxs])
        for (x0, k0), (x1, k1) in zip(allx, allx[1:]):
            if x1 - x0 < gap:
                raise ParamError(f"x={x0:.1f} 와 x={x1:.1f} 의 간격({x1-x0:.0f} mm)이 최소 "
                                 f"간격 {gap:.0f} mm 보다 작습니다 (늑골/격벽 겹침).")
        for i, x in enumerate(fxs):
            if not (xa < x < xf):
                raise ParamError(f"늑골 F{i+1} x={x:.1f} 이(가) 선체 범위 밖입니다.")
            fl = fr.get("flange") or {}
            flange = None
            if fl.get("enabled", True):
                sec = fl.get("section")
                sec = pick(sec, i, len(fxs), "frames.flange.section") if isinstance(sec, list) else sec
                flange = check_section(sec, "frames.flange.section")
            st = Station(f"F{i+1}", "frame", x,
                         _num(interp(fr.get("thickness"), x, "frames.thickness"), "frames.thickness"),
                         web_depth=interp(fr.get("web_depth"), x, "frames.web_depth"),
                         deck_beam_depth=interp(fr.get("deck_beam_depth"), x, "frames.deck_beam_depth"),
                         corner_radius=interp(fr.get("corner_radius", 0.0), x, "frames.corner_radius"),
                         flange=flange, index=i + 1)
            if st.thickness <= 0 or st.web_depth <= 0 or st.deck_beam_depth < 0:
                raise ParamError(f"F{i+1}: 두께/웨브 깊이는 0보다 커야 합니다.")
            if st.corner_radius < 0:
                raise ParamError("frames.corner_radius 는 0 이상")
            L.stations.append(st)
    L.stations.sort(key=lambda s: s.x)
    refs = L.station_x()

    # ---------------- girders
    girders = p.get("girders") or []
    if not isinstance(girders, list):
        raise ParamError("girders: 목록이어야 합니다.")
    gabs = []
    for gi, g in enumerate(girders):
        nm = g.get("name") or f"Girder{gi+1}"
        y = _num(g.get("y"), f"girders[{nm}].y")
        t = _num(g.get("thickness"), f"girders[{nm}].thickness")
        if t <= 0:
            raise ParamError(f"girders[{nm}].thickness 는 0보다 커야 합니다.")
        segs = []
        for si, s in enumerate(g.get("segments") or []):
            x0 = resolve_x(s.get("x_from", "aft"), refs, f"girders[{nm}].segments[{si}].x_from")
            x1 = resolve_x(s.get("x_to", "fwd"), refs, f"girders[{nm}].segments[{si}].x_to")
            zt = s.get("z_top")
            interp(zt, 0.0, f"girders[{nm}].segments[{si}].z_top")  # validates
            if x1 <= x0:
                raise ParamError(f"girders[{nm}] 구간 {si+1}: x_from({x0:.0f}) < x_to({x1:.0f}) 이어야 합니다.")
            segs.append((max(x0, xa - 1), min(x1, xf + 1), zt,
                         ref_of(s.get("x_from", "aft")), ref_of(s.get("x_to", "fwd"))))
        if not segs:
            raise ParamError(f"girders[{nm}]: segments 가 비어 있습니다.")
        fl = g.get("flange") or {}
        flange = check_section(fl.get("section"), f"girders[{nm}].flange.section") \
            if fl.get("enabled", False) else None
        sides = [y, -y] if g.get("symmetric", True) and abs(y) > 1e-6 else [y]
        for sgn_y in sides:
            suffix = "" if len(sides) == 1 else ("_P" if sgn_y > 0 else "_S")
            L.girders.append(Girder(nm + suffix, sgn_y, t, segs, flange))
        gabs.append(abs(y))
    ys = sorted(set(round(g.y, 3) for g in L.girders))
    for a, b in zip(ys, ys[1:]):
        if b - a < float(bmin.get("min_member_gap", 30.0)):
            raise ParamError(f"거더 y={a} 와 y={b} 가 너무 가깝습니다.")

    # ---------------- plates
    for pi_, pl in enumerate(p.get("plates") or []):
        nm = pl.get("name") or f"Plate{pi_+1}"
        x0 = resolve_x(pl.get("x_from", "aft"), refs, f"plates[{nm}].x_from")
        x1 = resolve_x(pl.get("x_to", "fwd"), refs, f"plates[{nm}].x_to")
        y0 = _num(pl.get("y_from"), f"plates[{nm}].y_from")
        y1 = _num(pl.get("y_to"), f"plates[{nm}].y_to")
        if x1 <= x0 or y1 <= y0:
            raise ParamError(f"plates[{nm}]: from < to 이어야 합니다.")
        t = _num(pl.get("thickness"), f"plates[{nm}].thickness")
        L.plates.append(Plate(nm, _num(pl.get("z"), f"plates[{nm}].z"),
                              max(x0, xa - 1), min(x1, xf + 1), y0, y1, t,
                              ref_of(pl.get("x_from")), ref_of(pl.get("x_to"))))

    # ---------------- stringers
    sg = p["stringers"]
    for grp, host, axis in (("bottom", "bottom", "y"), ("deck", "deck", "y"),
                            ("side", "side", "z")):
        cfg = sg.get(grp) or {}
        vals = _series(cfg, f"stringers.{grp}", gabs if grp != "side" else ())
        if grp != "side":
            for v in vals:
                if v <= 0:
                    raise ParamError(f"stringers.{grp}: |y| 는 0보다 커야 합니다 "
                                     "(중심선은 centerline 항목 사용).")
                if v >= hull["half_beam"]:
                    raise ParamError(f"stringers.{grp}: |y|={v} 가 선체 반폭 "
                                     f"{hull['half_beam']:.0f} 이상입니다.")
                for g in gabs:
                    if abs(v - g) < float(bmin.get("min_member_gap", 30.0)):
                        L.warnings.append(f"stringers.{grp} |y|={v} 가 거더 y={g} 와 매우 가깝습니다.")
        else:
            for v in vals:
                if not hull["z_keel"] < v < hull["z_deck"]:
                    raise ParamError(f"stringers.side: z={v} 가 선체 높이 범위 밖입니다.")
        for a, b in zip(vals, vals[1:]):
            if b - a < float(bmin.get("min_member_gap", 30.0)):
                raise ParamError(f"stringers.{grp}: {a} 와 {b} 간격이 너무 작습니다.")
        x0 = resolve_x(cfg.get("x_from", "aft"), refs, f"stringers.{grp}.x_from")
        x1 = resolve_x(cfg.get("x_to", "fwd"), refs, f"stringers.{grp}.x_to")
        ext = cfg.get("extents")
        if isinstance(ext, list) and ext and len(ext) != len(vals):
            L.warnings.append(f"stringers.{grp}.extents 개수({len(ext)})가 stringer 수"
                              f"({len(vals)})와 달라 무시하고 x_from/x_to 를 사용합니다.")
            ext = None
        secs = cfg.get("section", DEFAULT_SECTION_T)
        r0g, r1g = ref_of(cfg.get("x_from", "aft")), ref_of(cfg.get("x_to", "fwd"))
        for i, v in enumerate(vals):
            a, b = x0, x1
            r0, r1 = r0g, r1g
            if isinstance(ext, list) and ext:
                e = pick(ext, i, len(vals), f"stringers.{grp}.extents")
                if not (isinstance(e, list) and len(e) == 2):
                    raise ParamError(f"stringers.{grp}.extents: [[x_from, x_to], ...] 형식")
                a = resolve_x(e[0], refs, f"stringers.{grp}.extents")
                b = resolve_x(e[1], refs, f"stringers.{grp}.extents")
                r0, r1 = ref_of(e[0]), ref_of(e[1])
            sec = check_section(pick(secs, i, len(vals), f"stringers.{grp}.section")
                                if isinstance(secs, list) else secs,
                                f"stringers.{grp}.section")
            if grp == "side":
                # one plane z=v cuts port and starboard sides at once
                L.beams.append(BeamLine(f"Stringer_Side{i+1}", "stringer_side",
                                        "side", "z", v, a, b, section=sec,
                                        x0_ref=r0, x1_ref=r1))
            else:
                for sgn, tag in ((1, "P"), (-1, "S")):
                    L.beams.append(BeamLine(f"Stringer_{grp.capitalize()}{i+1}_{tag}",
                                            f"stringer_{grp}", host, "y", sgn * v, a, b,
                                            section=sec, x0_ref=r0, x1_ref=r1))

    # ---------------- centerline beams
    for k, host in (("keel", "bottom"), ("deck", "deck")):
        c = (p.get("centerline") or {}).get(k) or {}
        if c.get("enabled"):
            L.centerline[k] = check_section(c.get("section"), f"centerline.{k}.section")

    # ---------------- bulkhead stiffeners (pattern for every bulkhead)
    bst = bh.get("stiffeners") or {}
    per = bh.get("per_bulkhead") or []
    for s in [s for s in L.stations if s.kind == "bulkhead"]:
        pat = bst
        if isinstance(per, list) and len(per) >= s.index and per[s.index - 1]:
            pat = per[s.index - 1]
        if s.stiffeners:
            pat = s.stiffeners
        _stiff_pattern(L, pat, f"BH{s.index}", "bulkhead", s.name,
                       f"bulkheads.stiffeners", hull)

    # ---------------- transom stiffeners
    _stiff_pattern(L, p.get("transom") or {}, "Transom", "transom", None,
                   "transom", hull)


def auto_stations(first: float, last: float, spacing: float) -> List[float]:
    """Positions from the first to the last station with (nearly) the
    requested spacing: n = round((last-first)/spacing) intervals."""
    if not (last > first):
        raise ParamError("자동 배치: 마지막 위치가 첫 위치보다 커야 합니다.")
    if not spacing > 0:
        raise ParamError("자동 배치: 간격은 0보다 커야 합니다.")
    n = max(1, int(round((last - first) / spacing)))
    return [first + (last - first) * i / n for i in range(n + 1)]


def apply_auto_layout(p: dict) -> dict:
    """Regenerate stations.list from stations.auto (first, last, spacing,
    keep_bulkheads) - same rule as the GUI button.  Returns p (modified)."""
    st = p.setdefault("stations", {})
    a = st.get("auto") or {}
    old = list(st.get("list") or [])
    xs = auto_stations(_num(a.get("first"), "stations.auto.first"),
                       _num(a.get("last"), "stations.auto.last"),
                       _num(a.get("spacing"), "stations.auto.spacing"))
    rows = [{"x": round(x, 2), "type": "frame"} for x in xs]
    if a.get("keep_bulkheads", True):
        for b in [r for r in old if r.get("type") == "bulkhead"]:
            free = [i for i, r in enumerate(rows) if r["type"] != "bulkhead"]
            if not free:
                break
            k = min(free, key=lambda i: abs(rows[i]["x"] - b["x"]))
            rows[k] = copy.deepcopy(b)
    rows.sort(key=lambda r: r["x"])
    st["list"] = rows
    return p


def _table_stations(p, hull, L, gap):
    """stations.list = [{x, type: frame|bulkhead, thickness?, web_depth?, ...}]
    Missing per-row values are taken from the frames / bulkheads blocks."""
    xa, xf = hull["x_aft"], hull["x_fwd"]
    fr, bh = p["frames"], p["bulkheads"]
    rows = p["stations"]["list"]
    if not isinstance(rows, list):
        raise ParamError("stations.list 는 목록이어야 합니다.")
    items = []
    for k, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ParamError(f"stations.list[{k+1}]: {{x: .., type: frame|bulkhead}} 형식")
        t = str(r.get("type", "frame")).lower()
        if t not in ("frame", "bulkhead"):
            raise ParamError(f"stations.list[{k+1}]: type 은 frame 또는 bulkhead ({r.get('type')!r})")
        x = _num(r.get("x"), f"stations.list[{k+1}].x")
        if not (xa < x < xf):
            raise ParamError(f"횡부재 {k+1}번 x={x:.1f} 이(가) 선체 범위 ({xa:.0f} ~ {xf:.0f}) 밖입니다.")
        items.append((x, t, r))
    items.sort(key=lambda it: it[0])
    for (x0, _, _), (x1, _, _) in zip(items, items[1:]):
        if x1 - x0 < gap:
            raise ParamError(f"x={x0:.1f} 와 x={x1:.1f} 의 간격({x1-x0:.0f} mm)이 최소 간격 "
                             f"{gap:.0f} mm 보다 작습니다.")
    nb = sum(1 for it in items if it[1] == "bulkhead")
    ib = jf = 0
    fl_cfg = fr.get("flange") or {}
    for x, t, r in items:
        def val(key, src, what):
            v = r.get(key)
            if v is None:
                v = src
            return _num(interp(v, x, what), what)
        if t == "bulkhead":
            ib += 1
            thk_src = pick(bh.get("thickness"), ib - 1, nb, "bulkheads.thickness") \
                if r.get("thickness") is None else r.get("thickness")
            th = _num(thk_src, f"BH{ib}.thickness")
            if th <= 0:
                raise ParamError(f"BH{ib}: 두께는 0보다 커야 합니다.")
            st_ov = r.get("stiffeners") if isinstance(r.get("stiffeners"), dict) else None
            L.stations.append(Station(f"BH{ib}", "bulkhead", x, th, index=ib, stiffeners=st_ov))
        else:
            jf += 1
            nm = f"F{jf}"
            st = Station(nm, "frame", x,
                         val("thickness", fr.get("thickness"), f"{nm}.thickness"),
                         web_depth=val("web_depth", fr.get("web_depth"), f"{nm}.web_depth"),
                         deck_beam_depth=val("deck_beam_depth", fr.get("deck_beam_depth"), f"{nm}.deck_beam_depth"),
                         corner_radius=val("corner_radius", fr.get("corner_radius", 0.0), f"{nm}.corner_radius"),
                         index=jf)
            if st.thickness <= 0 or st.web_depth <= 0 or st.deck_beam_depth < 0 or st.corner_radius < 0:
                raise ParamError(f"{nm}: 두께/웨브 깊이는 0보다 커야 합니다.")
            fl_on = r.get("flange_enabled")
            fl_on = fl_cfg.get("enabled", True) if fl_on is None else bool(fl_on)
            if fl_on:
                sec = r.get("flange") or fl_cfg.get("section")
                if isinstance(sec, list):
                    sec = pick(sec, jf - 1, jf, f"{nm}.flange")
                st.flange = check_section(sec, f"{nm}.flange")
            L.stations.append(st)


def _stiff_pattern(L, pat, prefix, host, station, what, hull, x0=-1e9, x1=1e9):
    k = 0
    for gi, v in enumerate(pat.get("vertical") or []):
        ys = _series(v, f"{what}.vertical[{gi}]")
        z0 = v.get("z_from", "hull")
        z1 = v.get("z_to", "deck")
        z0 = -1e9 if z0 in ("hull", None) else _num(z0, f"{what}.vertical[{gi}].z_from")
        z1 = 1e9 if z1 in ("deck", None) else _num(z1, f"{what}.vertical[{gi}].z_to")
        if z1 <= z0:
            raise ParamError(f"{what}.vertical[{gi}]: z_from < z_to 이어야 합니다.")
        sec = check_section(v.get("section", DEFAULT_SECTION_T), f"{what}.vertical[{gi}].section")
        sym = v.get("symmetric", True)
        for y in ys:
            sides = [y] if (abs(y) < 1e-6 or not sym) else [y, -y]
            for yy in sides:
                k += 1
                L.beams.append(BeamLine(f"{prefix}_V{k}", f"{host}_stiffener", host,
                                        "y", yy, x0, x1, z0, z1, section=sec,
                                        station=station))
    for gi, h in enumerate(pat.get("horizontal") or []):
        z = _num(h.get("z"), f"{what}.horizontal[{gi}].z")
        ym = h.get("y_max")
        ym = 1e9 if ym in (None, "full") else _num(ym, f"{what}.horizontal[{gi}].y_max")
        sec = check_section(h.get("section", DEFAULT_SECTION_T), f"{what}.horizontal[{gi}].section")
        if not hull["z_keel"] < z < hull["z_deck"]:
            raise ParamError(f"{what}.horizontal[{gi}]: z={z} 가 선체 높이 범위 밖입니다.")
        k += 1
        L.beams.append(BeamLine(f"{prefix}_H{k}", f"{host}_stiffener", host, "z", z,
                                x0, x1, y_abs_max=ym, section=sec, station=station))
