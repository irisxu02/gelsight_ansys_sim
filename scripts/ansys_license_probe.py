"""Query the licence server and report what it grants."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.diagnostics.license_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
