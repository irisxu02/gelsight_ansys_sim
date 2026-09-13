"""Launch the packaged export examples workflow."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.batch.export_examples import main

if __name__ == "__main__":
    main()
