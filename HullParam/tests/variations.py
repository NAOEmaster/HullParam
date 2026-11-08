"""Parameter-variation regression test.

    python tests/variations.py <project_dir> [case ...]

Each case modifies the extracted params, builds, checks and (optionally)
exports.  Expected: status OK/WARN with 0 gaps, 0 floating beams, 1 component,
or a clean ParamError for intentionally invalid input.
"""
import copy
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hullparam import params as P            # noqa: E402
from hullparam.build import build            # noqa: E402
from hullparam.check import check_model      # noqa: E402
from hullparam.export import export_all      # noqa: E402


def case_more(p):
    """more frames (auto 1000), moved bulkheads, spacing-mode stringers, angle"""
    p["stations"]["auto"]["spacing"] = 1000.0
    P.apply_auto_layout(p)
    for r in p["stations"]["list"]:
        if r["type"] == "bulkhead":
            r["x"] = round(r["x"] - 300.0, 2)
    p["stations"]["list"][2]["type"] = "bulkhead"
    for g in p["girders"]:
        for s in g["segments"]:
            for k in ("x_from", "x_to"):
                if isinstance(s[k], str) and s[k].startswith("F"):
                    s[k] = "BH2"
    sb = p["stringers"]["bottom"]
    sb.update(mode="auto", spacing=180.0, count=8, extents=None,
              skip_near_girders=60.0,
              section=[{"shape": "L", "height": 60, "width": 45, "t_web": 6, "t_flange": 6}] * 6)
    p["centerline"]["keel"]["enabled"] = True
    ss = p["stringers"]["side"]
    ss.update(mode="spacing", start=800.0, spacing=420.0, count=3, extents=None,
              section=ss["section"][0])
    sd = p["stringers"]["deck"]
    sd.update(mode="spacing", start=250.0, spacing=350.0, count=5, extents=None,
              section=sd["section"][0])
    p["plating"].update(deck=5.0, bottom=7.0)
    p["frames"]["thickness"] = 10.0
    return p


def case_fewer(p):
    """fewer frames (auto 2000), one bulkhead, girders ending at BH1/F4"""
    p["stations"]["auto"]["spacing"] = 2000.0
    p["stations"]["list"] = [r for r in p["stations"]["list"] if not (r["type"] == "bulkhead" and r["x"] > 9000)]
    P.apply_auto_layout(p)
    p["girders"][0]["segments"] = [{"x_from": "aft", "x_to": "F4",
                                    "z_top": p["girders"][0]["segments"][0]["z_top"]}]
    p["girders"][1]["segments"] = [{"x_from": "aft", "x_to": "BH1", "z_top": 733.3}]
    p["bulkheads"]["per_bulkhead"] = None
    for k in ("bottom", "side", "deck"):
        p["stringers"][k]["extents"] = None
    return p


def case_spacing(p):
    """legacy blocks: uniform frame spacing, no station table"""
    p["stations"]["list"] = []
    p["bulkheads"].update(mode="list", positions=[6637.9, 9758.5])
    p["frames"].update(mode="spacing", spacing=1000.0, min_gap_to_bulkhead=300.0)
    for g in p["girders"]:
        for s in g["segments"]:
            for k in ("x_from", "x_to"):
                if isinstance(s[k], str) and s[k].startswith("F"):
                    s[k] = "BH2"
    for k in ("bottom", "side", "deck"):
        p["stringers"][k]["extents"] = None
    return p


def case_invalid(p):
    """bad input must give a clear error, not a crash"""
    p["stations"]["list"][3]["x"] = p["stations"]["list"][2]["x"] + 20   # too close
    return p


CASES = {"more": case_more, "fewer": case_fewer, "spacing": case_spacing,
         "invalid": case_invalid}

if __name__ == "__main__":
    PD = sys.argv[1]
    names = sys.argv[2:] or list(CASES)
    base = P.load(os.path.join(PD, "params.yaml"))
    for n in names:
        p = CASES[n](copy.deepcopy(base))
        t = time.time()
        print(f"===== case {n}: {CASES[n].__doc__}")
        try:
            P.save(p, os.path.join(PD, f"params_{n}.yaml"))
            p = P.load(os.path.join(PD, f"params_{n}.yaml"))     # YAML round trip
            m = build(p, PD, log=lambda s: None)
            rep = check_model(m, log=print)
            for i in rep["issues"]:
                if i["level"] in ("ERROR", "WARN") and i["code"] != "C07":
                    print("   ", i["level"], i["code"], i["message"][:140])
            st = rep["stats"]
            print(f"   -> {rep['status']}  stations={len(m.layout.stations)} "
                  f"shell={st['shell_members']} beam={st['beam_members']} faces={st['faces']} "
                  f"gaps={st['gaps']} floating={st['floating_beam_edges']} comps={st['components']} "
                  f"small_e={st['small_edges']} small_f={st['small_faces']}  {time.time()-t:.0f}s")
            if n == "more":
                export_all(m, os.path.join(PD, "out_more"), rep, log=lambda s: None)
        except P.ParamError as e:
            print(f"   -> ParamError (expected for 'invalid'): {e}")
