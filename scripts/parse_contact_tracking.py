"""Summarize an ANSYS contact tracking (.cnd) file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.diagnostics.tracking_report import main

if __name__ == "__main__":
    raise SystemExit(main())
