"""Check the built native libraries against their analytic energy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.diagnostics.adapter_coupons import main

if __name__ == "__main__":
    raise SystemExit(main())
