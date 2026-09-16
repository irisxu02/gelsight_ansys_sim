"""Launch the packaged finite-element view renderer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.batch.render_mesh_views import main

if __name__ == "__main__":
    main()
