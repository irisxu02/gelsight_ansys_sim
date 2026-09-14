"""Read an ANSYS solution-history (.mntr) file.

The monitor file records, per converged substep, how many equilibrium
iterations it cost and how many attempts it took. A restart reads it to find
the last converged substep; the diagnostics read it for the cost trend, which
rises well before a solve aborts.
"""

from pathlib import Path

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
        raise ValueError(f"No converged substeps recorded in {path}")
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


def last_converged(monitor):
    """(load step, substep, solver time) of the last row in a .mntr file."""
    records = read_monitor(monitor)
    last = records[-1]
    return last["load_step"], last["substep"], last["time"]


def completed_steps(monitor, is_checkpoint):
    """Final (load step, substep, solver time) of each load step that finished.

    A restart point is written at the last substep of a load step, so a step
    interrupted part-way leaves nothing to restart from however many of its
    substeps converged. The solver's own index says so once MAPDL is open; the
    monitor says it earlier, which is where a resume has to decide: a step
    finished if a later one began, or if its last substep landed on the
    checkpoint it was solving towards.
    """
    steps = {}
    for row in read_monitor(monitor):
        steps.setdefault(row["load_step"], []).append(row)
    highest = max(steps)
    finished = []
    for step, rows in sorted(steps.items()):
        last = rows[-1]
        if step < highest or is_checkpoint(last["time"]):
            finished.append((step, last["substep"], last["time"]))
    return finished
