"""What a suite's own sampling and checkpoint grids come to.

Two test modules check the same contract from opposite ends - one that a mesh
and its case agree, one that a CLI override reaches the run - so the count they
compare against is derived here once, independently of the code under test.
"""

import numpy as np


def expected_count(case, coarse_step, key):
    """Frames or checkpoints: the coarse grid joined with each window's own.

    The suite integrates the slide with mass, and a window keeps its own
    sampling and checkpoint grid whatever the dataset-level interval is, so
    coarsening the CLI interval thins only the quasi-static stretches.
    """
    start, end = case.suite["protocol"]["recorded_interval_s"]
    grids = [np.linspace(start, end, round((end - start) / coarse_step) + 1)]
    for w in case.transient_windows:
        step = w.get(key, w["time_increment_s"] if key == "solve_interval_s" else None)
        if step is not None:
            a, b = w["start_time_s"], w["end_time_s"]
            grids.append(np.linspace(a, b, round((b - a) / step) + 1))
    return len(np.unique(np.round(np.concatenate(grids), 12)))
