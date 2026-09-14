"""Run the licensed mesh-import checks on small meshes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.batch.validate_mesh_import import main

if __name__ == "__main__":
    raise SystemExit(main())
