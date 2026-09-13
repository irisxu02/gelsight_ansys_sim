"""Summarize an ANSYS solution-history (.mntr) file.

The monitor file records, per converged substep, how many equilibrium iterations
it cost and how many attempts it took. Both rise well before a solve aborts, so
this turns a failed run into a trend that can be read without reopening ANSYS.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

COLUMNS = (
    "load_step",
    "substep",
    "attempts",
    "iterations",
    "cumulative_iterations",
    "time_increment",
    "time",
)


def read_monitor(path):
    """Parse the fixed numeric block, ignoring the three-line banner."""
    records = []
    for line in Path(path).read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 7:
            continue
        try:
            values = [int(v) for v in fields[:5]] + [float(v) for v in fields[5:7]]
        except ValueError:
            continue
        record = dict(zip(COLUMNS, values))
        # Monitor variables are optional and vary with the analysis.
        for name, index in (("max_displacement", 8), ("max_residual", 10)):
            if len(fields) > index:
                try:
                    record[name] = float(fields[index])
                except ValueError:
                    pass
        records.append(record)
    if not records:
        raise ValueError(f"No solution history rows found in {path}")
    return records


def summarize(records, window=10):
    """Report the cost trend and where the solver had to retry."""
    retried = [r for r in records if r["attempts"] > 1]
    tail = records[-window:]
    head = records[:window]
    return {
        "substeps": len(records),
        "load_steps": records[-1]["load_step"],
        "total_iterations": records[-1]["cumulative_iterations"],
        "final_time": records[-1]["time"],
        "retried_substeps": len(retried),
        "retried_at": [
            {
                "load_step": r["load_step"],
                "substep": r["substep"],
                "attempts": r["attempts"],
            }
            for r in retried
        ],
        "mean_iterations_first": sum(r["iterations"] for r in head) / len(head),
        "mean_iterations_last": sum(r["iterations"] for r in tail) / len(tail),
        "max_iterations": max(r["iterations"] for r in records),
        "smallest_time_increment": min(r["time_increment"] for r in records),
        "largest_time_increment": max(r["time_increment"] for r in records),
    }


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


if __name__ == "__main__":
    raise SystemExit(main())
