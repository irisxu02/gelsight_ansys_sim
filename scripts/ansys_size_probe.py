"""Check a licensed solver's model-size capacity with a cheap connected spring chain."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gelsight_ansys.config import Config
from gelsight_ansys.mechanics import AnsysGel


class SizeProbe(AnsysGel):
    def build(self):
        pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nodes", type=int, default=600000)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.nodes < 2:
        raise ValueError("At least two nodes are required")
    with SizeProbe(Config(), args.output) as model:
        a = model.mapdl
        commands = [
            "/PREP7",
            "ET,1,COMBIN14",
            "KEYOPT,1,2,1",
            "R,1,1000",
            "N,1,0,0,0",
            f"NGEN,{args.nodes},1,1,,,0",
            "E,1,2",
            f"EGEN,{args.nodes - 1},1,1",
            "D,ALL,UX,0",
            f"DDELE,{args.nodes},UX",
            f"F,{args.nodes},FX,1",
            "FINISH",
            "/SOLU",
            "ANTYPE,STATIC",
            "NCNV,2",
            "SOLVE",
            "FINISH",
        ]
        a.ignore_errors = True
        text = model.command_block(commands, "size_probe")
        failed = "*** ERROR ***" in text.upper()
        size_rejected = any(
            term in text.lower()
            for term in ("problem size limit", "limited to", "license limitation")
        )
        record = {
            "nodes": args.nodes,
            "elements": args.nodes - 1,
            "passed": not failed,
            "size_rejected": size_rejected,
        }
        (args.output / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
