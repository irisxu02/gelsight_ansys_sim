"""Render the frames a plane run solved but never wrote, then rebuild its report."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gelsight_ansys.batch.fill_frame import fill_missing_frames  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Finished run directory")
    parser.add_argument(
        "--nonmisc-base",
        type=int,
        help="CONTA174 NMISC offset, ETYIQR(2,-110); needed for runs that did not record it",
    )
    args = parser.parse_args(argv)
    filled = fill_missing_frames(args.run, args.nonmisc_base)
    print(f"{len(filled)} frame(s) filled; report rebuilt in {args.run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
