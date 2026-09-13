"""Launch the packaged compare rendering workflow."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.batch.compare_rendering import main

if __name__ == "__main__":
    main()
