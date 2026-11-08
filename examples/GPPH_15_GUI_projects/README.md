# GPPH: 15 GUI comparison cases

Fifteen projects based on `examples/GPPH_A4-B1_project`. The hull form and
unmodified parameters are identical to the repository's baseline example.
The three groups each contain one original and four modified projects:

| Group | Folders | Variable |
| --- | --- | --- |
| G | `G00_original` … `G04_outer_1525mm` | Port/starboard outer bottom girder `|y|` (mm): 1210.5 / 925 / 1125 / 1325 / 1525. Inner girder stays at `|y|=403.5`. |
| H | `H00_original` … `H04_plus_120mm` | Inner bottom girder top profile `z_top(x)` shifted by 0 / +30 / +60 / +90 / +120 mm, retaining its slope. |
| F | `F00_original` … `F04_1500mm` | Longitudinal positions of aft transverse frames F1–F4. Original spacing is about 1327.6 mm; variants use 1100 / 1200 / 1400 / 1500 mm. |

The source envelope is the same 7.4 MB BREP in all 15 cases. To keep the Git
repository small, it is stored once in `examples/GPPH_A4-B1_project/`.
Before opening these cases in the GUI, run from the repository root:

```bash
python examples/GPPH_15_GUI_projects/prepare_projects.py
```

On Windows, `py -3` can be used instead of `python`. The script copies the
baseline `envelope.brep`, `envelope.json`, and `extract_report.json` into each
project folder. It does not replace existing files and requires only Python's
standard library. These locally generated copies are ignored by Git.

Then run `run.bat` (Windows) or `./run.sh` and choose **Open project** → one of
the 15 case folders → **▶ Generate**. Re-generate after switching projects:
the GUI does not display an old `out/` file automatically. The `out/` folder
is written locally by generation and is ignored by Git. The precomputed
`cases.csv` records the original validation for all 15 cases; it does not
replace validating or generating on your machine.

All 15 precomputed checks report one connected component and zero gaps,
floating beam edges, and invalid shapes. Their WARN status includes the two
transom stiffener warnings already present in the original model.

Bottom girders are **shell surfaces** in this program. Their flanges are
separate **beam line bodies**. The viewer tessellation is for display; it is
not a structural FE mesh.
