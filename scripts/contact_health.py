"""Trend contact health across a run's saved frames."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.diagnostics.contact_health import main

if __name__ == "__main__":
    raise SystemExit(main())
