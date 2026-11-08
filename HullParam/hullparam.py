#!/usr/bin/env python3
"""HullParam command line.

  python hullparam.py                         # GUI (빈 화면에서 CAD 가져오기)
  python hullparam.py app  <project>          # GUI with a project
  python hullparam.py extract <cad.step> [-m model.mph] [-o project_dir]
  python hullparam.py build   <project> [-p params.yaml] [-o out_dir]
  python hullparam.py batch   <project> <cases.yaml> [-o out_dir]

`build` = generate + integrity check + export (STEP/IGES/BREP/CSV/JSON).
`batch` = many parameter variants in one run (dataset generation), see
          examples/batch_example.yaml.
"""
import argparse
import copy
import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _deps_ok():
    missing = []
    for mod, pkg in (("OCP", "cadquery-ocp"), ("shapely", "shapely"), ("yaml", "pyyaml"),
                     ("numpy", "numpy")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("필요한 패키지가 없습니다: " + ", ".join(missing))
        print("설치:  python -m pip install -r requirements.txt   (또는 install.bat 실행)")
        return False
    return True


def cmd_app(a):
    from hullparam.app import serve
    serve(a.project or "", a.port, not a.no_browser)


def cmd_extract(a):
    from hullparam.extract import extract
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.cad)),
                                os.path.splitext(os.path.basename(a.cad))[0] + "_project")
    extract(a.cad, out, a.mph, sew_tol=a.sew, name=a.name)
    print(f"\n다음 단계:  python hullparam.py app \"{out}\"   (또는 build)")


def _build_one(project, params, out_dir, log=print):
    from hullparam.build import build
    from hullparam.check import check_model
    from hullparam.export import export_all
    m = build(params, project, log=log)
    rep = check_model(m, log=log)
    files = export_all(m, out_dir, rep, log=log)
    return m, rep, files


def cmd_build(a):
    from hullparam import params as P
    pf = a.params or os.path.join(a.project, "params.yaml")
    p = P.load(pf)
    out = a.out or os.path.join(a.project, "output", time.strftime("%Y%m%d_%H%M%S"))
    m, rep, files = _build_one(a.project, p, out)
    print(f"\n상태: {rep['status']}  →  {out}")
    for i in rep["issues"]:
        if i["level"] == "ERROR":
            print("  ERROR", i["code"], i["message"])
    sys.exit(2 if rep["status"] == "ERROR" else 0)


def set_dotted(d, key, value):
    """'frames.per_compartment' / 'girders.0.thickness' style assignment."""
    parts = key.split(".")
    o = d
    for k in parts[:-1]:
        o = o[int(k)] if isinstance(o, list) else o.setdefault(k, {})
    last = parts[-1]
    if isinstance(o, list):
        o[int(last)] = value
    else:
        o[last] = value


def cmd_batch(a):
    import yaml
    from hullparam import params as P
    with open(a.cases, encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)
    base_file = spec.get("base") or os.path.join(a.project, "params.yaml")
    if not os.path.isabs(base_file):
        base_file = os.path.join(a.project, base_file)
    base = P.load(base_file)
    out_root = a.out or os.path.join(a.project, "batch_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_root, exist_ok=True)
    rows = []
    for k, case in enumerate(spec.get("cases") or [], 1):
        name = case.get("name") or f"case{k:03d}"
        p = copy.deepcopy(base)
        p["meta"]["name"] = name
        for key, val in (case.get("set") or {}).items():
            set_dotted(p, key, val)
        if case.get("auto_stations") or any(k.startswith("stations.auto") for k in (case.get("set") or {})):
            P.apply_auto_layout(p)
        t = time.time()
        print(f"===== [{k}/{len(spec['cases'])}] {name}")
        row = {"case": name, **{f"set:{kk}": str(vv) for kk, vv in (case.get("set") or {}).items()}}
        try:
            m, rep, files = _build_one(a.project, p, os.path.join(out_root, name),
                                       log=lambda s: None)
            st = rep["stats"]
            row.update(status=rep["status"], faces=st["faces"], beam_edges=st["beam_edges"],
                       gaps=st["gaps"], components=st["components"],
                       shell_area_m2=st["shell_area_m2"], beam_length_m=st["beam_length_m"],
                       seconds=round(time.time() - t, 1), error="")
            print(f"   {rep['status']}  ({row['seconds']} s)")
        except Exception as e:
            row.update(status="FAILED", error=str(e), seconds=round(time.time() - t, 1))
            print(f"   FAILED: {e}")
        rows.append(row)
    cols = []
    for r in rows:
        for c in r:
            if c not in cols:
                cols.append(c)
    with open(os.path.join(out_root, "batch_summary.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\n완료: {os.path.join(out_root, 'batch_summary.csv')}")


def main():
    ap = argparse.ArgumentParser(prog="hullparam", description="HullParam - 선체 구조 파라메트릭 CAD 생성기")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("app", help="GUI 실행")
    s.add_argument("project", nargs="?")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_app)
    s = sub.add_parser("extract", help="CAD에서 파라미터 자동 추출")
    s.add_argument("cad")
    s.add_argument("-m", "--mph", help="COMSOL .mph (두께/단면/보강재)")
    s.add_argument("-o", "--out", help="프로젝트 폴더")
    s.add_argument("--sew", type=float, default=0.5, help="외판 꿰매기 공차 mm")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_extract)
    s = sub.add_parser("build", help="생성 + 검사 + 내보내기")
    s.add_argument("project")
    s.add_argument("-p", "--params")
    s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_build)
    s = sub.add_parser("batch", help="여러 파라미터 케이스 일괄 생성")
    s.add_argument("project")
    s.add_argument("cases")
    s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_batch)
    a = ap.parse_args()
    if not _deps_ok():
        sys.exit(1)
    if not a.cmd:
        a = ap.parse_args(["app"])
    a.fn(a)


if __name__ == "__main__":
    main()
