# HullParam — Parametric Hull-Structure CAD Generator (Shell + Beam)

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![OpenCASCADE](https://img.shields.io/badge/OpenCASCADE-7.9%20%28cadquery--ocp%29-1f5fae)](https://github.com/CadQuery/OCP)
[![Shapely](https://img.shields.io/badge/Shapely-2.x-4b8bbe)](https://shapely.readthedocs.io/)
[![NumPy](https://img.shields.io/badge/NumPy-013243?logo=numpy&logoColor=white)](https://numpy.org/)
[![three.js](https://img.shields.io/badge/three.js-r160-000000?logo=threedotjs&logoColor=white)](https://threejs.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#1-installation-once)
[![Output](https://img.shields.io/badge/export-STEP%20%7C%20IGES%20%7C%20BREP-orange)](#6-output-files-outputtimestamp)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Feed in a **complete hull CAD model (skin + internal structure)** and HullParam recognizes the structural members
and turns them into a **parameter file (`params.yaml`)**. Change the parameters (plate thicknesses, number/spacing of
frames and bulkheads, number/spacing/section of stiffeners, …) and a **complete, watertight CAD model** (shells + beams)
is regenerated, **checked for integrity**, and exported as **STEP / IGES / BREP**. Everything can be inspected and edited
in an ANSYS-like 3D viewer that runs in your browser.

- Geometry kernel: OpenCASCADE (`cadquery-ocp`, open source) — no COMSOL or ANSYS license required
- Input: STEP (recommended) / IGES / BREP (+ optional COMSOL `.mph` → thicknesses, stiffener sections,
  bulkhead/transom stiffener positions; read directly from the file, COMSOL is **not** needed)
- Output: named STEP assembly (shells + beams), shell STEP, beam STEP/IGES, topology-preserving BREP,
  member property table (CSV), mapping/report (JSON)

A detailed explanation of the algorithm and of how each open-source library is used is in
[`docs/HullParam_algorithm.pdf`](docs/HullParam_algorithm.pdf) (Korean).

```
 STEP (+ .mph) ─► ① skin → closed solid ─► ② member recognition ─► ③ params.yaml
                                                                      │  (edit in GUI / batch)
 STEP/IGES/BREP/CSV/JSON ◄─ ⑦ export ◄─ ⑥ integrity check ◄─ ⑤ General Fuse ◄─ ④ regenerate members
```

---

## 1. Installation (once)

1. Install **Python 3.10 – 3.14 (64-bit)** — https://www.python.org/downloads/ (on Windows tick *Add python.exe to PATH*).
2. Double-click **`install.bat`** in this folder (macOS/Linux: `./install.sh`).
   - On Windows the virtual environment is created in **`%LOCALAPPDATA%\HullParam\venv`** to avoid long-path and
     OneDrive sync problems (the program folder itself may live in a OneDrive path with spaces / non-ASCII characters).
   - About 200 MB of packages, 1–3 minutes. Everything is logged to **`install_log.txt`**.

Manual install: `python -m pip install -r requirements.txt`
(`cadquery-ocp==7.9.3.1.1`, `numpy`, `shapely`, `pyyaml`).

## 2. Running

- Double-click **`run.bat`** (or `./run.sh`) → the app opens in your browser at `http://127.0.0.1:8765`
  (local only; keep the console window open while you work).
- Drag a project folder onto `run.bat` to open it directly.
- Example: `examples/GPPH_A4-B1_project` (extracted from the GPPH A4-B1 high-speed craft CAD + COMSOL model)
  → open it with **📂 Open project**.

## 3. Workflow (GUI)

| Step | Button | What it does |
|---|---|---|
| ① | **⤓ Import CAD** | Choose the full CAD (STEP) and optionally a COMSOL `.mph` → automatic extraction (1–2 min). Creates a `<CAD name>_project` folder. |
| ② | **Parameters** panel (right) | Tabs: Layout (frame/bulkhead table) / Longitudinals (girders, flats) / Stiffeners / Thickness & material / YAML (raw editing) |
| ③ | **✓ Validate** | Checks the input without building geometry and previews frame/bulkhead planes in 3D |
| ④ | **▶ Generate** | Save → build geometry → integrity check (GPPH: ~5 s – 2 min, with progress bar) |
| ⑤ | **Check results** (bottom) | Click an item marked 📍 to zoom to and highlight the location |
| ⑥ | **⇪ Export** | STEP/IGES/BREP/CSV/JSON → `<project>/output/<timestamp>/` |

Viewer: left-drag rotate, right/middle-drag pan, wheel zoom, click to select (thickness / section / area shown in
*Details*), double-click a tree item to zoom, `F` fit all, `Esc` clear selection, `H` hide selection.
Toolbar: standard views, edges/beams on-off, skin transparency, **color by thickness / section area**,
**section clipping at x**, isolate.

---

## 4. Parameters (`params.yaml`)

Units mm. Coordinates: x forward (+), y port (+), z up (+). Symmetric members are given once with |y|.

### x references
Start/end positions of girders, flats and stringers may be numbers or references:
**`aft`, `fwd`, `BH1..BHn` (bulkheads), `F1..Fn` (frames)**, with offsets such as `F3+20`.
Referenced ends follow the member when it moves and are cut exactly on that member's real surface.
> Changing the number of frames changes which frame `F5` refers to; prefer `BHn` references if you vary frame counts often.

### Position-dependent values (tables)
A single number or `{x: [..], value: [..]}` (linear interpolation, clamped outside the range). In the GUI: `1327.6:207.3, 2655.2:210.7`.
When the number of frames changes, each new frame gets the interpolated thickness / web depth at its position.

### Frame & bulkhead table `stations` (GUI tab **Layout**)
Frames and bulkheads are managed in **one table**. Each row has `x`, `type` (`frame` / `bulkhead`), `thickness`,
and for frames `web_depth`, `deck_beam_depth`, `corner_radius`; for bulkheads `stiffeners`.
Empty cells inherit the common values in `frames` / `bulkheads`.

| GUI section | Function |
|---|---|
| ① Auto layout | Enter **first x, last x and target spacing** → count = round((last − first) / spacing), actual spacing made uniform. With *keep bulkheads* on, existing bulkheads stay and replace the nearest frame. |
| ② Section preview | 2D view of the selected row's real section (hull section + opening) with areas |
| ③ Table | Name (F1.., BH1..) · type (**frame ↔ bulkhead**) · x · **gap to previous** (editing it shifts all following rows) · thickness · web depth · deck-beam depth · corner R · add (＋) / delete (✕) |
| ④ Bulk edit | Set thickness / web / deck beam / R for all frames at once |
| ⑤ Flange | Beam section along the frame opening edge |

If `stations.list` is empty, the legacy modes of `bulkheads` / `frames` are used.

### Bulkheads `bulkheads` / frames `frames` (common values + legacy modes)
- `bulkheads.thickness`, `stiffeners`: common stiffener pattern (`vertical`: |y| list or start/spacing/count,
  `z_from` / `z_to` (`hull` / `deck` allowed), section; `horizontal`: z, `y_max`).
  Priority: row `stiffeners` > `per_bulkhead[i]` > common.
- `frames`: common `thickness`, `web_depth`, `deck_beam_depth`, `corner_radius`, `flange`.
- Legacy modes: `frames.mode: per_compartment | spacing | list`, `bulkheads.mode: list | equal`.
- Frame geometry: hull section minus an opening obtained by offsetting the section inward by `web_depth`,
  closed by a `deck_beam_depth` band under the deck, corners rounded with `corner_radius`
  (automatically reduced, with a warning, if the opening is too narrow).

### Girders `girders`
`y` (|y|), `symmetric`, `thickness`, `segments: [{x_from, x_to, z_top}]` — `z_top` is a number or a table
(sloped top, e.g. engine ramp); `flange`: top beam.

### Flats `plates`
`z`, `x_from` / `x_to` (references allowed), `y_from` / `y_to`, `thickness` (e.g. engine seating).

### Longitudinal stiffeners `stringers` (beams, GUI tab **Stiffeners**)
- `bottom`, `deck`: |y| positions / `side`: z positions
- **`mode: auto`**: tick **include centerline (y = 0)** + **spacing** + **count per half** → positions
  spacing × 1, × 2, … (positions within `skip_near_girders` of a girder are skipped).
  The centerline option maps to the `centerline.keel` (bottom) / `centerline.deck` (deck) beams.
- `mode: list` (`positions`) / `mode: spacing` (`start`, `spacing`, `count`)
- `x_from` / `x_to`, or per-stiffener `extents` (lengths from the original CAD — ignored automatically when the count changes)
- `section`: one section or one per stiffener. Shapes **T** (`height, width, t_web, t_flange`),
  **L (angle)** (same fields), **FLAT** (`height, thickness`). In the GUI each stiffener's shape and size can be chosen,
  with **"same for this group"** and **"same for all stiffeners"** (applies one section to the ticked groups) buttons.

> **Stiffeners are beam elements.** Their geometry is the axis line lying on the plate (shared with the plate's
> edges after the fuse); the cross-section is stored as a property. When importing into an FE program, assign the
> section from the CSV and apply a **beam offset** (½ plate thickness + centroid height) so the stiffener sits on
> the plate surface rather than on the shell mid-plane.

### Other
`centerline` (keel / deck centerline beams), `transom` (transom stiffeners, same format as bulkheads),
`plating` (skin thicknesses: deck / side / bottom / chine / transom), `material`, `build` (tolerances, check limits).

---

## 5. Integrity checks (run automatically on every build)

| Code | Check | Level |
|---|---|---|
| C01 | Geometric validity (BRepCheck). Near-tangent parameter-space overlaps are re-checked by meshing and reported as WARN if the 3D face is fine | ERROR / WARN |
| C02 | Missing shell thickness / beam section | ERROR |
| C03 | Floating beam (not attached to any plate) | ERROR |
| C04 | Disconnected part of the structure | ERROR |
| C05 | Gap: a plate edge almost touches a neighbour but is not connected | ERROR |
| C06 | Same face owned by two members (overlap) | WARN |
| C07 | Short edges / small faces (mesh quality) | WARN |
| B00 | Build-time warnings (skipped members, automatic corrections, …) | WARN |
| C08 | Statistics (T-joints, free edges) | INFO |

To avoid sliver faces the generator automatically:
- snaps members lying within `snap_tolerance` (2 mm) of an existing skin seam onto that seam
  (including the slightly tilted COMSOL frame planes of GPPH);
- repairs edge-curve tolerances on B-spline skin faces after the boolean operation and reports the resulting tolerance.

## 6. Output files (`output/<timestamp>/`)

| File | Purpose |
|---|---|
| `<name>_assembly.step` | **Recommended for ANSYS.** Named, colored members under SHELLS / BEAMS |
| `<name>_shells.step` | All shell faces (one body) |
| `<name>_beams.step`, `_beams.igs` | Beam lines (line bodies in DesignModeler / SpaceClaim) |
| `<name>.brep` | Exact model with shared edges between members preserved (OpenCASCADE / gmsh) |
| `<name>_properties.csv` | Per member: type, thickness, section (dimensions, A, Iyy, Izz), area / length |
| `<name>_model.json` | Member → BREP face / edge indices + properties (for meshing / Nastran conversion / graph datasets) |
| `<name>_check.json`, `_params.yaml` | Check report, parameters used |

**In ANSYS:** open `_assembly.step` in SpaceClaim (or DesignModeler) → assign beam sections from the CSV →
**Share Topology** (required, because STEP cannot store shared edges between bodies). Shell thickness = `thickness_mm` in the CSV.

## 7. Batch generation (datasets)

```
python hullparam.py batch <project> examples/batch_example.yaml
```
(on Windows use `%LOCALAPPDATA%\HullParam\venv\Scripts\python.exe`.)
Each case lists only the keys to change under `set:` (`stations.auto.spacing: 1000`,
`stations.list.4.type: bulkhead`, `girders.0.thickness: 9`, …). Changing any `stations.auto.*` key rebuilds the
station table from first / last / spacing (bulkheads kept). One folder per case plus `batch_summary.csv`
(status, area, length, time) is written.

Command line:
```
python hullparam.py                                   # GUI
python hullparam.py app     <project>                 # GUI with a project
python hullparam.py extract <cad.step> [-m model.mph] [-o project_dir]
python hullparam.py build   <project> [-p params.yaml] [-o out_dir]   # generate + check + export
python hullparam.py batch   <project> <cases.yaml> [-o out_dir]
```

Tests: `python tests/smoke.py <project> <params.yaml> <out_dir>` (area regression against the source CAD),
`python tests/variations.py <project>` (more/fewer frames, legacy modes, invalid input).

## 8. Validation on GPPH A4-B1

- Frame areas within 0.4–2.4 % of the source CAD, bulkheads within 0.01 %; total shell area 156.45 m² vs 156.35 m² in the original Nastran model.
- Baseline model: 431 faces, 756 beam edges, 360 T-joints, 0 gaps, 0 invalid faces, 0 floating beams, 1 connected component (~4 s).
- Frame-opening fit (IoU) 0.95–0.97; recovered corner radius 200 mm (COMSOL `F_FilletRadius` = 200.6 mm).

## 9. Limitations

- **The hull skin is fixed.** The input skin is reused as is; seams in the original skin (old frame / stringer
  positions) remain as face boundaries. Members within 2 mm are snapped onto them; farther ones create ordinary
  boundaries. A clean, unsplit skin STEP gives cleaner results.
- `.x_t` (Parasolid) and `.sat` (ACIS) cannot be read by the open-source kernel → use a **STEP** export of the same model.
- Frames are modeled as "skin offset + deck beam + corner rounding"; lightening holes and brackets are not represented.
- COMSOL transom stiffener coordinates are an approximate conversion from work-plane coordinates
  (lines falling outside the transom are skipped with a warning).
- Shell IGES export is off by default because B-spline face accuracy can degrade (use STEP).

## Acknowledgements

Built on [OpenCASCADE](https://dev.opencascade.org/) via [cadquery-ocp](https://github.com/CadQuery/OCP),
[Shapely](https://shapely.readthedocs.io/), [NumPy](https://numpy.org/), [PyYAML](https://pyyaml.org/) and
[three.js](https://threejs.org/) (vendored, MIT). The GPPH model was provided by its original author for research use.

## License

HullParam is released under the [MIT License](LICENSE). The GPPH example data in `examples/GPPH_A4-B1_project/`
belongs to its original author and is **not** covered by this license. Vendored three.js keeps its MIT license
(`hullparam/web/vendor/three.LICENSE`); OpenCASCADE (via cadquery-ocp) is LGPL-2.1 with exception and is installed separately.
