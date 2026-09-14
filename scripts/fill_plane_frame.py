"""Render the frames a plane run solved but never wrote, then rebuild its report."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.batch.fill_plane_frame import main

if __name__ == "__main__":
    raise SystemExit(main())
