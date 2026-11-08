"""Prepare the 15 GPPH parameter cases for HullParam's project-open dialog.

The hull envelope is identical in every case and already tracked in the
repository's baseline example. Copy it locally rather than storing 15 copies
of the same 7.4 MB BREP in Git.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "GPPH_A4-B1_project"
SHARED = ("envelope.brep", "envelope.json", "extract_report.json")


def main() -> None:
    with (HERE / "cases.csv").open(encoding="utf-8-sig", newline="") as stream:
        cases = list(csv.DictReader(stream))
    if len(cases) != 15 or len({case["folder"] for case in cases}) != 15:
        raise SystemExit("cases.csv must contain exactly 15 distinct projects")

    for name in SHARED:
        if not (BASELINE / name).is_file():
            raise SystemExit(f"Missing baseline envelope file: {BASELINE / name}")

    created = 0
    for case in cases:
        folder = case["folder"]
        if Path(folder).name != folder or folder in (".", ".."):
            raise SystemExit(f"Invalid case directory: {folder}")
        project = HERE / folder
        if not (project / "params.yaml").is_file():
            raise SystemExit(f"Missing parameters: {project / 'params.yaml'}")
        for name in SHARED:
            target = project / name
            if not target.exists():
                shutil.copy2(BASELINE / name, target)
                created += 1
    print(f"Prepared {len(cases)} GPPH GUI projects ({created} local source files copied).")
    print("Open any case folder in HullParam and click ▶ Generate.")


if __name__ == "__main__":
    main()
