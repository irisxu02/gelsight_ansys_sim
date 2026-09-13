"""Compatibility launcher for the shared resume command."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gelsight_ansys.cli import main

if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and not arguments[0].startswith("-"):
        arguments = ["--run", *arguments]
    raise SystemExit(main(["resume", *arguments]))
