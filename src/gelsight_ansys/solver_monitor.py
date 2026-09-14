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


def restartable_steps(monitor):
    """Final (load step, substep, solver time) of each load step a restart can reach.

    The restart point for a load step is written after its solve returns, so a
    step is only known to have one once the next step has begun: reaching its
    own target time is not enough, and a run killed in between leaves a step
    whose substeps all converged and whose restart file never appeared. Asking
    for that step is refused by the solver's index, which is the same knowledge
    arriving too late to act on.

    The cost of being wrong the safe way is one checkpoint; the cost of being
    wrong the other way is the whole resume.
    """
    steps = {}
    for row in read_monitor(monitor):
        steps.setdefault(row["load_step"], []).append(row)
    highest = max(steps)
    return [
        (step, rows[-1]["substep"], rows[-1]["time"])
        for step, rows in sorted(steps.items())
        if step < highest
    ]
