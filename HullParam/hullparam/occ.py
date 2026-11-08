"""Thin, version-safe helpers around OpenCASCADE (OCP / cadquery-ocp).

Every OCC call used by the rest of the package goes through this module, so a
change of OCP version only needs fixes here.  Tested with cadquery-ocp 7.8.x;
the TopoDS casts below also cover 7.9 / 8.0 where the ``_s`` suffix was removed.
"""
from __future__ import annotations

import math
import os
from typing import Iterable, List, Sequence, Tuple

import numpy as np

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import (BRepAlgoAPI_Common, BRepAlgoAPI_Cut,
                             BRepAlgoAPI_Section)
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GProp import GProp_GProps
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GeomAbs import GeomAbs_Plane
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import (TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE,
                        TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID,
                        TopAbs_VERTEX, TopAbs_WIRE)
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt


# --------------------------------------------------------------------------
def quiet_occ() -> None:
    """Silence OpenCASCADE's console statistics (STEP/IGES transfer logs)."""
    try:
        from OCP.Message import Message, Message_Gravity
        for p in Message.DefaultMessenger_s().Printers():
            p.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception:
        pass


quiet_occ()


# --------------------------------------------------------------------------
# casts (OCP 7.8 uses TopoDS.Face_s, 7.9+/8.0 use TopoDS.Face)
# --------------------------------------------------------------------------
def _cast(name):
    return getattr(TopoDS, name + "_s", None) or getattr(TopoDS, name)


as_face = _cast("Face")
as_edge = _cast("Edge")
as_wire = _cast("Wire")
as_shell = _cast("Shell")
as_solid = _cast("Solid")
as_vertex = _cast("Vertex")
as_compound = _cast("Compound")

KIND = {"face": TopAbs_FACE, "edge": TopAbs_EDGE, "wire": TopAbs_WIRE,
        "shell": TopAbs_SHELL, "solid": TopAbs_SOLID, "vertex": TopAbs_VERTEX}
CAST = {"face": as_face, "edge": as_edge, "wire": as_wire, "shell": as_shell,
        "solid": as_solid, "vertex": as_vertex}


class GeometryError(RuntimeError):
    """Raised when an OCC operation fails; message is meant for the user."""


# --------------------------------------------------------------------------
# exploration
# --------------------------------------------------------------------------
def explore(shape: TopoDS_Shape, kind: str, avoid: str | None = None) -> list:
    """All sub-shapes of a kind in OCC traversal order (may contain the same
    sub-shape several times when it is shared; use ``unique`` to dedupe)."""
    out = []
    ex = (TopExp_Explorer(shape, KIND[kind], KIND[avoid]) if avoid
          else TopExp_Explorer(shape, KIND[kind]))
    cast = CAST[kind]
    while ex.More():
        out.append(cast(ex.Current()))
        ex.Next()
    return out


def unique(shapes: Iterable[TopoDS_Shape]) -> list:
    """Remove duplicates (IsSame) from a list of shapes."""
    buckets: dict = {}
    out = []
    for s in shapes:
        h = s.__hash__()
        lst = buckets.setdefault(h, [])
        if any(s.IsSame(o) for o in lst):
            continue
        lst.append(s)
        out.append(s)
    return out


def faces(shape) -> list:
    return unique(explore(shape, "face"))


def edges(shape) -> list:
    return unique(explore(shape, "edge"))


def count(shape, kind: str) -> int:
    return len(unique(explore(shape, kind)))


def compound(shapes: Iterable[TopoDS_Shape]) -> TopoDS_Compound:
    c = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(c)
    for s in shapes:
        if s is not None and not s.IsNull():
            b.Add(c, s)
    return c


def edge_face_map(shape) -> TopTools_IndexedDataMapOfShapeListOfShape:
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def list_of_shape(lst) -> list:
    """TopTools_ListOfShape -> python list (works on all OCP versions)."""
    try:
        return [s for s in lst]
    except TypeError:  # very old bindings
        out = []
        it = lst.begin()
        while it != lst.end():
            out.append(it.Value())
            it.Next()
        return out


# --------------------------------------------------------------------------
# measures
# --------------------------------------------------------------------------
def bbox(shape, optimal: bool = True) -> np.ndarray:
    """[xmin, ymin, zmin, xmax, ymax, zmax] (tight when optimal=True)."""
    b = Bnd_Box()
    if optimal:
        BRepBndLib.AddOptimal_s(shape, b, False, False)
    else:
        BRepBndLib.Add_s(shape, b)
    if b.IsVoid():
        return np.array([np.nan] * 6)
    p, q = b.CornerMin(), b.CornerMax()
    return np.array([p.X(), p.Y(), p.Z(), q.X(), q.Y(), q.Z()])


def area(shape) -> float:
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return g.Mass()


def length(shape) -> float:
    g = GProp_GProps()
    BRepGProp.LinearProperties_s(shape, g)
    return g.Mass()


def volume(shape) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def centroid(shape, dim: int = 2) -> np.ndarray:
    g = GProp_GProps()
    if dim == 2:
        BRepGProp.SurfaceProperties_s(shape, g)
    elif dim == 1:
        BRepGProp.LinearProperties_s(shape, g)
    else:
        BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass()
    return np.array([c.X(), c.Y(), c.Z()])


def is_valid(shape) -> bool:
    try:
        return bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception:
        return False


# --------------------------------------------------------------------------
# surfaces / curves
# --------------------------------------------------------------------------
def plane_info(face):
    """(True, normal, origin) for planar faces, else (False, None, None)."""
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_Plane:
        return False, None, None
    pl = ad.Plane()
    d, o = pl.Axis().Direction(), pl.Location()
    return True, np.array([d.X(), d.Y(), d.Z()]), np.array([o.X(), o.Y(), o.Z()])


def face_normal(face, point: Sequence[float] | None = None) -> np.ndarray:
    """Unit normal of a face, honouring its orientation (outward for a valid
    solid).  Evaluated at the projection of ``point`` (default: centroid)."""
    if point is None:
        point = centroid(face)
    surf = BRep_Tool.Surface_s(face)
    proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*map(float, point)), surf)
    if proj.NbPoints() == 0:
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        u, v = (u0 + u1) / 2, (v0 + v1) / 2
    else:
        u, v = proj.LowerDistanceParameters()
    pr = BRepLProp_SLProps(BRepAdaptor_Surface(face), u, v, 1, 1e-6)
    if not pr.IsNormalDefined():
        return np.array([0.0, 0.0, 0.0])
    n = pr.Normal()
    v = np.array([n.X(), n.Y(), n.Z()])
    if face.Orientation() == TopAbs_REVERSED:
        v = -v
    return v / (np.linalg.norm(v) or 1.0)


def edge_points(edge, n: int = 9) -> np.ndarray:
    c = BRepAdaptor_Curve(edge)
    ts = np.linspace(c.FirstParameter(), c.LastParameter(), n)
    out = []
    for t in ts:
        p = c.Value(t)
        out.append((p.X(), p.Y(), p.Z()))
    return np.array(out)


def edge_discretize(edge, deflection: float = 0.05) -> np.ndarray:
    """Points along an edge with chordal deviation <= deflection (mm),
    in the edge's own orientation."""
    c = BRepAdaptor_Curve(edge)
    d = GCPnts_QuasiUniformDeflection(c, deflection)
    if not d.IsDone() or d.NbPoints() < 2:
        pts = edge_points(edge, 50)
    else:
        pts = np.array([[d.Value(i).X(), d.Value(i).Y(), d.Value(i).Z()]
                        for i in range(1, d.NbPoints() + 1)])
    if edge.Orientation() == TopAbs_REVERSED:
        pts = pts[::-1]
    return pts


# --------------------------------------------------------------------------
# primitive builders
# --------------------------------------------------------------------------
AXES = {"x": 0, "y": 1, "z": 2}


def plane_face(axis: str, value: float, half: float = 1.0e5):
    """A huge square planar face normal to the given global axis."""
    n = [0.0, 0.0, 0.0]
    n[AXES[axis]] = 1.0
    o = [0.0, 0.0, 0.0]
    o[AXES[axis]] = float(value)
    pln = gp_Pln(gp_Pnt(*o), gp_Dir(*n))
    return BRepBuilderAPI_MakeFace(pln, -half, half, -half, half).Face()


def box(xmin, ymin, zmin, xmax, ymax, zmax):
    """Axis aligned box solid (sizes are made strictly positive)."""
    eps = 1e-3
    if xmax - xmin < eps:
        xmax = xmin + eps
    if ymax - ymin < eps:
        ymax = ymin + eps
    if zmax - zmin < eps:
        zmax = zmin + eps
    return BRepPrimAPI_MakeBox(gp_Pnt(xmin, ymin, zmin),
                               gp_Pnt(xmax, ymax, zmax)).Shape()


def segment(p, q):
    return BRepBuilderAPI_MakeEdge(gp_Pnt(*map(float, p)),
                                   gp_Pnt(*map(float, q))).Edge()


# --------------------------------------------------------------------------
# booleans with error reporting
# --------------------------------------------------------------------------
def _run(op, name):
    if hasattr(op, "HasErrors") and op.HasErrors():
        raise GeometryError(f"OpenCASCADE {name} failed")
    if not op.IsDone():
        raise GeometryError(f"OpenCASCADE {name} did not complete")
    return op.Shape()


def common(a, b, fuzzy: float = 0.0):
    op = BRepAlgoAPI_Common()
    op.SetArguments(_lst([a]))
    op.SetTools(_lst([b]))
    if fuzzy:
        op.SetFuzzyValue(fuzzy)
    op.Build()
    return _run(op, "common")


def cut(a, b, fuzzy: float = 0.0):
    op = BRepAlgoAPI_Cut()
    op.SetArguments(_lst([a]))
    op.SetTools(_lst([b]))
    if fuzzy:
        op.SetFuzzyValue(fuzzy)
    op.Build()
    return _run(op, "cut")


def section(a, b):
    op = BRepAlgoAPI_Section(a, b, False)
    op.ComputePCurveOn1(True)
    op.Approximation(True)
    op.Build()
    return _run(op, "section")


def _lst(shapes):
    from OCP.TopTools import TopTools_ListOfShape
    l = TopTools_ListOfShape()
    for s in shapes:
        l.Append(s)
    return l


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------
def read_cad(path: str) -> TopoDS_Shape:
    """Read STEP / IGES / BREP.  STEP and IGES are converted to millimetres."""
    ext = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        raise GeometryError(f"파일을 찾을 수 없습니다: {path}")
    if ext in (".step", ".stp"):
        Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        r = STEPControl_Reader()
        if r.ReadFile(path) != IFSelect_RetDone:
            raise GeometryError(f"STEP 파일을 읽지 못했습니다: {path}")
        r.TransferRoots()
        return r.OneShape()
    if ext in (".igs", ".iges"):
        Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        r = IGESControl_Reader()
        if r.ReadFile(path) != IFSelect_RetDone:
            raise GeometryError(f"IGES 파일을 읽지 못했습니다: {path}")
        r.TransferRoots()
        return r.OneShape()
    if ext in (".brep", ".brp"):
        return read_brep(path)
    if ext in (".x_t", ".x_b", ".sat", ".sab"):
        raise GeometryError(
            f"{ext} 형식은 오픈소스 커널(OpenCASCADE)로 읽을 수 없습니다. "
            "같은 모델의 STEP(.step) 또는 IGES(.igs) 파일을 사용하세요.")
    raise GeometryError(f"지원하지 않는 형식: {ext} (STEP/IGES/BREP 사용)")


def read_brep(path: str) -> TopoDS_Shape:
    s = TopoDS_Shape()
    ok = BRepTools.Read_s(s, path, BRep_Builder())
    if not ok or s.IsNull():
        raise GeometryError(f"BREP 파일을 읽지 못했습니다: {path}")
    return s


def write_brep(shape, path: str) -> None:
    if not BRepTools.Write_s(shape, path):
        raise GeometryError(f"BREP 저장 실패: {path}")


# --------------------------------------------------------------------------
# tessellation (viewer)
# --------------------------------------------------------------------------
def mesh_shape(shape, lin_defl: float = 2.0, ang_defl: float = 0.35):
    BRepMesh_IncrementalMesh(shape, lin_defl, False, ang_defl, True)


def face_triangles(face) -> Tuple[np.ndarray, np.ndarray]:
    """Vertices (n,3) and triangles (m,3) of an already meshed face, with the
    triangle winding following the face orientation."""
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    trsf = loc.Transformation()
    n = tri.NbNodes()
    v = np.empty((n, 3))
    for i in range(1, n + 1):
        p = tri.Node(i).Transformed(trsf)
        v[i - 1] = (p.X(), p.Y(), p.Z())
    m = tri.NbTriangles()
    t = np.empty((m, 3), dtype=np.int32)
    rev = face.Orientation() == TopAbs_REVERSED
    for i in range(1, m + 1):
        a, b, c = tri.Triangle(i).Get()
        t[i - 1] = (a - 1, c - 1, b - 1) if rev else (a - 1, b - 1, c - 1)
    return v, t


def edge_polyline(edge, deflection: float = 1.0) -> np.ndarray:
    return edge_discretize(edge, deflection)
