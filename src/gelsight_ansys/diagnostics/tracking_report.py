"""Report what a contact tracking file says about interface stability."""

import argparse
import json
import sys
from pathlib import Path

from ..contact_tracking import per_step, read_tracking, summarize


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tracking", type=Path, help="Path to a job .cnd file")
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Subtract this from solver time to get physical time (plane runs: 2.0)",
    )
    parser.add_argument("--tail", type=int, default=15, help="Load-step rows to print")
    parser.add_argument("--json", type=Path, help="Write the per-step rows here")
    args = parser.parse_args(argv)
    rows = read_tracking(args.tracking)
    steps = per_step(rows)
    if args.tail:
        print(
            f"{'time':>9} {'step':>5} {'chatter':>8} {'contact':>8} {'sticking':>9} "
            f"{'overpen':>8} {'area mm2':>9}"
        )
        for r in steps[-args.tail :]:
            print(
                f"{r['time'] - args.offset:9.4f} {r['load_step']:5d} "
                f"{r['chattering']:8d} {r['in_contact']:8d} {r['sticking']:9d} "
                f"{r['over_penetrated_points']:8d} {r['contact_area_m2'] * 1e6:9.2f}"
            )
    summary = summarize(rows, args.offset)
    print(json.dumps(summary, indent=2))
    if summary["peak_chattering"] >= 6:
        print(
            f"Contact status chattered up to level {summary['peak_chattering']} at "
            f"time {summary['peak_chattering_at']:.4f}. A quiet interface stays at "
            "0-2; sustained levels above that mean points are changing status "
            "between iterations faster than Newton can settle them.",
            file=sys.stderr,
        )
    if args.json:
        args.json.write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")
    return 0

