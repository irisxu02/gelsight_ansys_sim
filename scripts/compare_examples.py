"""Plot the sphere examples' forces and marker motion against each other."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.batch.compare_examples import main

if __name__ == "__main__":
    raise SystemExit(main())
