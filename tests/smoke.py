"""Build + check + export + re-import for a project dir.  Usage:
   python tests/smoke.py <project_dir> [params.yaml]"""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hullparam import params as P, occ
from hullparam.build import build
from hullparam.check import check_model
from hullparam.export import export_all
PD = sys.argv[1]
pf = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PD, 'params.yaml')
out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(PD, 'out')
t = time.time()
m = build(P.load(pf), PD)
rep = check_model(m)
for i in rep['issues']:
    if i['level'] != 'INFO' or True:
        print(' ', i['level'], i['code'], i['message'][:150], i.get('location', ''))
# regression: with unchanged station positions every frame / bulkhead must
# reproduce the original CAD member area (frames within 6 %, bulkheads 1 %)
import json as _j
_rp = os.path.join(PD, 'extract_report.json')
if os.path.isfile(_rp):
    cad = {s['x']: s.get('cad_area_m2') for s in _j.load(open(_rp, encoding='utf-8'))['stations']}
    bad = []
    for mem in m.members:
        if mem.category in ('frame', 'bulkhead'):
            x = mem.props['x']
            k = min(cad, key=lambda c: abs(c - x)) if cad else None
            if k is None or abs(k - x) > 3 or not cad[k]:
                continue
            a = sum(occ.area(f) for f in mem.shapes) / 1e6
            tol = 0.06 if mem.category == 'frame' else 0.01
            flag = abs(a - cad[k]) / cad[k] > tol
            print('  area %-4s x=%8.1f  built %.4f  CAD %.4f  %s' % (mem.name, x, a, cad[k], 'MISMATCH' if flag else 'ok'))
            if flag:
                bad.append(mem.name)
    print('AREA REGRESSION:', 'FAIL ' + str(bad) if bad else 'PASS')
files = export_all(m, out, rep)
# re-import verification
for f in files:
    if f.endswith(('.step', '.igs', '.brep')):
        s = occ.read_cad(f)
        print('  reimport %-28s faces %5d edges %5d valid=%s' % (os.path.basename(f), occ.count(s, 'face'), occ.count(s, 'edge'), occ.is_valid(s)))
print('STATUS', rep['status'], json.dumps(rep['stats']))
print('total %.1f s' % (time.time() - t))
