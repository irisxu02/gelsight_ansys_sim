"""Report the cost trend a solution-history file records."""

import argparse
import json
import sys
from pathlib import Path

from ..solver_monitor import read_monitor, summarize


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("monitor", type=Path, help="Path to a job .mntr file")
    parser.add_argument("--json", type=Path, help="Write the parsed rows here")
    parser.add_argument(
        "--tail", type=int, default=12, help="Substep rows to print (0 for none)"
    )
    args = parser.parse_args(argv)
    records = read_monitor(args.monitor)
    summary = summarize(records)
    if args.tail:
        print(f"{'step':>5} {'sub':>4} {'try':>4} {'iter':>5} {'dt':>11} {'time':>10}")
        for r in records[-args.tail :]:
            print(
                f"{r['load_step']:5d} {r['substep']:4d} {r['attempts']:4d} "
                f"{r['iterations']:5d} {r['time_increment']:11.4g} {r['time']:10.4f}"
            )
    print(json.dumps(summary, indent=2))
    if summary["mean_iterations_last"] > 2 * summary["mean_iterations_first"]:
        print(
            "Iteration cost more than doubled toward the end: equilibrium was getting "
            "harder to reach before the run stopped.",
            file=sys.stderr,
        )
    if args.json:
        args.json.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return 0

