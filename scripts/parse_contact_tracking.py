"""Summarize an ANSYS contact tracking (.cnd) file.

NLDIAG,CONT,ITER writes one row per equilibrium iteration per contact pair. The
column that matters for a slide that will not converge is the chattering level:
how many times a contact point changed status within the substep. It rises with
sliding distance long before the solve aborts, and unlike the force residual it
says what is unstable rather than only that something is.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

# Column order is fixed by the CND format and declared in the file's own header.
FIELDS = (
    ("pair", 0, int),
    ("load_step", 2, int),
    ("substep", 3, int),
    ("iteration", 5, int),
    ("time", 6, float),
    ("chattering", 7, int),
    ("in_contact", 8, int),
    ("sticking", 9, int),
    ("max_penetration_element", 10, int),
    ("max_penetration_target", 11, int),
    ("over_penetrated_points", 12, int),
    ("over_slid_points", 13, int),
    ("contact_area_m2", 16, float),
)


def read_tracking(path):
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) <= max(index for _, index, _ in FIELDS):
            continue
        try:
            rows.append({name: cast(fields[i]) for name, i, cast in FIELDS})
        except ValueError:
            continue  # Header and markup lines.
    if not rows:
        raise ValueError(f"No contact tracking rows found in {path}")
    return rows


def per_step(rows):
    """The worst iteration of each load step, which is what stalls a solve."""
    worst = collections.OrderedDict()
    for row in rows:
        key = row["load_step"]
        if key not in worst or row["chattering"] > worst[key]["chattering"]:
            worst[key] = row
    return list(worst.values())


def summarize(rows, offset):
    steps = per_step(rows)
    peak = max(steps, key=lambda r: r["chattering"])
    area = [r["contact_area_m2"] for r in rows]
    return {
        "iterations": len(rows),
        "load_steps": len(steps),
        "time_span": [rows[0]["time"] - offset, rows[-1]["time"] - offset],
        "peak_chattering": peak["chattering"],
        "peak_chattering_at": peak["time"] - offset,
        "steps_above_5": sum(1 for r in steps if r["chattering"] > 5),
        "max_over_penetrated_points": max(r["over_penetrated_points"] for r in rows),
        "contact_area_span_mm2": [min(area) * 1e6, max(area) * 1e6],
    }


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


if __name__ == "__main__":
    raise SystemExit(main())
