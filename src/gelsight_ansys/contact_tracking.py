"""Read an ANSYS contact tracking (.cnd) file.

NLDIAG,CONT,ITER writes one row per equilibrium iteration per contact pair. The
column that matters for a slide that will not converge is the chattering level:
how many times a contact point changed status within the substep. It rises with
sliding distance long before the solve aborts, and unlike the force residual it
says what is unstable rather than only that something is.
"""

import collections
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
