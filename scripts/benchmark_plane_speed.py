"""Compare timestep and object-mesh candidates on identical preload."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.diagnostics.plane_speed_benchmark import main

if __name__ == "__main__":
    raise SystemExit(main())
