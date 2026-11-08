"""Local web application (ANSYS-like viewer + parameter editor).

    python hullparam.py app [project_dir] [--port 8765] [--no-browser]

Runs a small HTTP server on 127.0.0.1 only; the browser UI talks to it.
Long jobs (extract / build / export) run in a background thread, one at a time.
"""
from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from . import params as P
from .occ import GeometryError

WEB = os.path.join(os.path.dirname(__file__), "web")


class State:
    def __init__(self, project: str):
        self.project = os.path.abspath(project) if project else ""
        self.model = None
        self.check = None
        self.mesh = None
        self.mesh_ver = 0
        self.job = {"running": False, "name": "", "progress": 0.0, "message": "",
                    "log": [], "error": None, "result": None, "started": 0.0}
        self.lock = threading.Lock()

    # --------------------------------------------------------------
    @property
    def params_path(self):
        return os.path.join(self.project, "params.yaml")

    def has_envelope(self):
        return bool(self.project) and os.path.isfile(os.path.join(self.project, "envelope.brep"))

    def summary(self):
        d = {"project": self.project, "has_envelope": self.has_envelope(),
             "has_params": os.path.isfile(self.params_path) if self.project else False,
             "version": __version__, "mesh_ver": self.mesh_ver,
             "check": self.check, "job": self.job_public()}
        if d["has_params"]:
            with open(self.params_path, encoding="utf-8") as fh:
                d["yaml"] = fh.read()
            try:
                d["params"] = P.loads(d["yaml"])
            except Exception as e:
                d["params_error"] = str(e)
        rep = os.path.join(self.project, "extract_report.json") if self.project else ""
        if rep and os.path.isfile(rep):
            with open(rep, encoding="utf-8") as fh:
                r = json.load(fh)
            d["extract"] = {"warnings": r.get("warnings", []),
                            "defaults_used": r.get("defaults_used", []),
                            "frames_rule": r.get("frames_rule"),
                            "members": r.get("members", {})}
        if self.model is not None:
            d["timings"] = {k: round(v, 1) for k, v in self.model.timings.items()}
            d["snaps"] = self.model.log
        return d

    def job_public(self):
        j = dict(self.job)
        j["log"] = j["log"][-400:]
        j["elapsed"] = round(time.time() - j["started"], 1) if j["started"] else 0
        return j

    # --------------------------------------------------------------
    def start(self, name, fn):
        with self.lock:
            if self.job["running"]:
                raise RuntimeError(f"다른 작업({self.job['name']})이 실행 중입니다.")
            self.job = {"running": True, "name": name, "progress": 0.0, "message": "시작",
                        "log": [], "error": None, "result": None, "started": time.time()}

        def log(msg):
            self.job["log"].append(str(msg))

        def progress(frac, msg):
            self.job["progress"] = float(frac)
            self.job["message"] = msg

        def run():
            try:
                self.job["result"] = fn(log, progress)
            except (GeometryError, P.ParamError, ValueError) as e:
                self.job["error"] = str(e)
                log("오류: " + str(e))
            except Exception as e:  # unexpected -> keep the traceback for support
                self.job["error"] = f"예상하지 못한 오류: {e}"
                log(traceback.format_exc())
            finally:
                self.job["running"] = False
                self.job["progress"] = 1.0 if not self.job["error"] else self.job["progress"]
        threading.Thread(target=run, daemon=True).start()


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        server_version = "HullParam/" + __version__

        def log_message(self, *a):   # keep the console quiet
            pass

        # ---------------- helpers
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def _err(self, msg, code=400):
            self._send(code, {"ok": False, "error": msg})

        # ---------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            path = unquote(u.path)
            if path in ("/", "/index.html"):
                return self._file(os.path.join(WEB, "index.html"))
            if path.startswith("/web/"):
                fp = os.path.normpath(os.path.join(WEB, path[5:]))
                if not fp.startswith(WEB):
                    return self._err("bad path", 403)
                return self._file(fp)
            if path == "/api/state":
                return self._send(200, state.summary())
            if path == "/api/job":
                return self._send(200, state.job_public())
            if path == "/api/mesh":
                if state.mesh is None and state.has_envelope():
                    from .envelope import Envelope
                    from .viewer import envelope_mesh
                    state.mesh = envelope_mesh(Envelope.load(state.project))
                    state.mesh["preview"] = True
                return self._send(200, state.mesh or {"members": []})
            if path == "/api/frame_preview":
                q = parse_qs(u.query)
                try:
                    return self._send(200, _frame_preview(state, {k: v[0] for k, v in q.items()}))
                except Exception as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if path == "/api/browse":
                q = parse_qs(u.query)
                return self._send(200, _browse(q.get("dir", [""])[0],
                                               q.get("ext", [""])[0]))
            return self._err("not found", 404)

        def _file(self, fp):
            if not os.path.isfile(fp):
                return self._err("not found", 404)
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            if fp.endswith(".js"):
                ctype = "text/javascript; charset=utf-8"
            with open(fp, "rb") as fh:
                self._send(200, fh.read(), ctype)

        # ---------------- POST
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self._json()
            except Exception:
                return self._err("JSON 형식 오류")
            try:
                if path == "/api/validate":
                    return self._send(200, _validate(state, body, save=False))
                if path == "/api/params":
                    return self._send(200, _validate(state, body, save=True))
                if path == "/api/build":
                    return self._send(200, _start_build(state, body))
                if path == "/api/export":
                    return self._send(200, _start_export(state, body))
                if path == "/api/extract":
                    return self._send(200, _start_extract(state, body))
                if path == "/api/project":
                    return self._send(200, _set_project(state, body))
                if path == "/api/open":
                    _open_path(body.get("path") or state.project)
                    return self._send(200, {"ok": True})
            except RuntimeError as e:
                return self._err(str(e), 409)
            except Exception as e:
                return self._err(f"{e}", 500)
            return self._err("not found", 404)

    return H


# ----------------------------------------------------------------------
_SEC_CACHE: dict = {}


def _frame_preview(state: State, q):
    """2-D section + opening of a frame at x (for the shape editor)."""
    from .envelope import Envelope
    from . import occ, profile2d as p2
    if not state.has_envelope():
        return {"ok": False, "error": "프로젝트 없음"}
    key_env = state.project
    env = _SEC_CACHE.get(("env", key_env))
    if env is None:
        env = Envelope.load(state.project)
        _SEC_CACHE.clear()
        _SEC_CACHE[("env", key_env)] = env
    x = float(q.get("x"))
    k = (key_env, round(x, 1))
    S = _SEC_CACHE.get(k)
    if S is None:
        sec = occ.common(occ.plane_face("x", x), env.solid)
        S = p2.shape_to_polygon(sec, "x", deflection=0.5)
        _SEC_CACHE[k] = S
    kind = q.get("type", "frame")
    z_deck = env.info["z_deck"]

    def rings(g):
        out = []
        for pg in p2.polygons(g.simplify(1.0)):
            out.append([[round(a, 1), round(b, 1)] for a, b in pg.exterior.coords])
            for h in pg.interiors:
                out.append([[round(a, 1), round(b, 1)] for a, b in h.coords])
        return out
    res = {"ok": True, "x": x, "section": rings(S), "section_area_m2": round(S.area / 1e6, 4),
           "z_deck": z_deck, "bounds": [round(v, 1) for v in S.bounds]}
    if kind == "frame":
        p2.ROUNDING_NOTES.clear()
        O = p2.frame_opening(S, float(q.get("web", 200)), float(q.get("deck", 100)),
                             z_deck, float(q.get("r", 0) or 0))
        res["opening"] = rings(O)
        res["frame_area_m2"] = round((S.area - O.area) / 1e6, 4)
        if p2.ROUNDING_NOTES:
            res["note"] = f"개구부가 좁아 라운드 반경을 {min(n[1] for n in p2.ROUNDING_NOTES)} mm 로 줄여 생성됩니다."
        if O.is_empty:
            res["note"] = "웨브 깊이가 단면보다 커서 개구부가 없습니다 (막힌 판이 됩니다)."
    return res


def _validate(state: State, body, save: bool):
    if not state.has_envelope():
        return {"ok": False, "errors": ["프로젝트에 외판 데이터가 없습니다. 먼저 CAD를 가져오세요."]}
    from .envelope import Envelope
    try:
        if "yaml" in body:
            p = P.loads(body["yaml"])
        else:
            p = P.deep_merge(P.DEFAULTS, body.get("params") or {})
        with open(os.path.join(state.project, "envelope.json"), encoding="utf-8") as fh:
            hull = json.load(fh)["info"]
        lay = P.expand(p, hull)
    except (P.ParamError, ValueError, GeometryError) as e:
        return {"ok": False, "errors": [str(e)]}
    except Exception as e:  # YAML syntax etc.
        return {"ok": False, "errors": [f"파라미터를 읽을 수 없습니다: {e}"]}
    if save:
        P.save(p, state.params_path)
    return {"ok": True, "warnings": lay.warnings, "saved": save,
            "params": p, "yaml": P.dumps(p),
            "counts": {"bulkheads": sum(s.kind == "bulkhead" for s in lay.stations),
                       "frames": sum(s.kind == "frame" for s in lay.stations),
                       "girders": len(lay.girders), "plates": len(lay.plates),
                       "beams": len(lay.beams)},
            "stations": [{"name": s.name, "x": round(s.x, 2), "kind": s.kind,
                          "thickness": round(s.thickness, 3),
                          "web_depth": round(s.web_depth, 2),
                          "deck_beam_depth": round(s.deck_beam_depth, 2),
                          "corner_radius": round(s.corner_radius, 2)} for s in lay.stations]}


def _start_build(state: State, body):
    if body.get("yaml") or body.get("params"):
        v = _validate(state, body, save=True)
        if not v["ok"]:
            return v

    def job(log, progress):
        from .build import build
        from .check import check_model
        from .viewer import model_mesh
        p = P.load(state.params_path)
        m = build(p, state.project, log=log, progress=lambda f, s: progress(0.9 * f, s))
        progress(0.92, "무결성 검사")
        rep = check_model(m, log=log)
        progress(0.96, "화면 표시용 메시 생성")
        mesh = model_mesh(m, float(p["build"].get("viewer_deflection", 3.0)), rep["issues"])
        state.model, state.check, state.mesh = m, rep, mesh
        state.mesh_ver += 1
        progress(1.0, "완료")
        return {"status": rep["status"], "stats": rep["stats"]}
    state.start("build", job)
    return {"ok": True, "started": True}


def _start_export(state: State, body):
    if state.model is None:
        return {"ok": False, "errors": ["먼저 형상을 생성(Generate)하세요."]}
    out = body.get("out_dir") or os.path.join(
        state.project, "output", time.strftime("%Y%m%d_%H%M%S"))
    formats = tuple(body.get("formats") or ("step", "iges", "brep"))
    iges_shells = bool(body.get("iges_shells", False))

    def job(log, progress):
        from .export import export_all
        progress(0.1, "내보내는 중")
        files = export_all(state.model, out, state.check, log=log, formats=formats,
                           iges_shells=iges_shells)
        return {"out_dir": out, "files": files}
    state.start("export", job)
    return {"ok": True, "started": True, "out_dir": out}


def _start_extract(state: State, body):
    cad = (body.get("cad") or "").strip().strip('"')
    mph = (body.get("mph") or "").strip().strip('"') or None
    proj = (body.get("project") or "").strip().strip('"')
    if not cad:
        return {"ok": False, "errors": ["CAD 파일 경로를 입력하세요."]}
    if not proj:
        proj = os.path.join(os.path.dirname(os.path.abspath(cad)),
                            os.path.splitext(os.path.basename(cad))[0] + "_project")
    sew = float(body.get("sew_tol") or 0.5)

    def job(log, progress):
        from .extract import extract
        progress(0.05, "CAD 분석 중 (1~2분)")
        extract(cad, proj, mph, sew_tol=sew, log=log)
        state.project = os.path.abspath(proj)
        state.model = state.check = state.mesh = None
        state.mesh_ver += 1
        return {"project": state.project}
    state.start("extract", job)
    return {"ok": True, "started": True, "project": proj}


def _set_project(state: State, body):
    d = (body.get("project") or "").strip().strip('"')
    if not os.path.isdir(d):
        return {"ok": False, "errors": [f"폴더가 없습니다: {d}"]}
    if not os.path.isfile(os.path.join(d, "envelope.brep")):
        return {"ok": False, "errors": ["프로젝트 폴더가 아닙니다 (envelope.brep 없음)."]}
    state.project = os.path.abspath(d)
    state.model = state.check = state.mesh = None
    state.mesh_ver += 1
    return {"ok": True, "project": state.project}


def _browse(d: str, ext: str):
    """Minimal file browser for the 'open' dialogs (local machine only)."""
    d = d or os.path.expanduser("~")
    d = os.path.abspath(d)
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    exts = [e.lower() for e in ext.split(",") if e]
    items = []
    try:
        for n in sorted(os.listdir(d), key=str.lower):
            if n.startswith("."):
                continue
            fp = os.path.join(d, n)
            if os.path.isdir(fp):
                items.append({"name": n, "dir": True,
                              "project": os.path.isfile(os.path.join(fp, "envelope.brep"))})
            elif not exts or os.path.splitext(n)[1].lower() in exts:
                items.append({"name": n, "dir": False, "size": os.path.getsize(fp)})
    except PermissionError:
        pass
    drives = []
    if os.name == "nt":
        import string
        drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return {"dir": d, "parent": os.path.dirname(d), "items": items, "drives": drives}


def _open_path(p):
    if not p or not os.path.exists(p):
        return
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def serve(project: str = "", port: int = 8765, open_browser: bool = True):
    state = State(project)
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), make_handler(state))
            port = p
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("사용 가능한 포트를 찾지 못했습니다.")
    url = f"http://127.0.0.1:{port}/"
    print(f"HullParam {__version__} 실행 중: {url}  (종료: Ctrl+C)")
    if project:
        print(f"프로젝트: {state.project}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("종료합니다.")
