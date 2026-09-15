"""One statement of when a run is a finished dataset.

Four commands used to decide this independently: the pipeline when it labels a
run, the plane validator before it records a pass, the exporter before it
copies, and the auditor after. Each knew a slightly different rule, so a run
could be exportable but not auditable, or be labelled passed while its own
summary said it stopped early. They all ask here now.

The checks read a run's saved summary and resolved configuration as plain
dictionaries, because that is what a finished run leaves behind; nothing here
needs the configuration to still be loadable under current validation rules.
"""

import math


def is_plane_record(config):
    """Whether a saved configuration describes a plane run."""
    return (
        config.get("config_kind") == "resolved_material_plane_run"
        or (config.get("indenter") or {}).get("shape") in ("plane", "cylinder")
    )


def completion_status(summary):
    """The status a run earns once every check has passed.

    A diagnostic run varied convergence controls part-way, so its frames are
    solver evidence rather than a dataset; a pilot stopped short or used a
    coarse mesh. Only a production run over the whole recorded interval passes.
    """
    if summary.get("diagnostic_run"):
        return "diagnostic_passed"
    if summary.get("production_mesh", True) and summary.get(
        "complete_recorded_interval", True
    ):
        return "passed"
    return "pilot_passed"


def dataset_errors(summary, config):
    """Why this run is not a complete dataset; empty when it is one.

    A dataset is a run that passed every check it declared, over the whole
    trajectory it declared, with the mesh it declared. Anything else - a pilot,
    a diagnostic, a run stopped early, a run whose frames do not line up with
    its own protocol - is evidence about the solver, not data to publish.
    """
    errors = []
    frames = summary.get("frames", [])
    expected = len(config.get("trajectory", ()))
    status = summary.get("status")
    if status != "passed":
        errors.append(f"status is {status!r}, not 'passed'")
    if len(frames) != expected:
        errors.append(f"{len(frames)} frames recorded, {expected} in the trajectory")
    if not is_plane_record(config):
        return errors
    for key in ("production_mesh", "complete_recorded_interval"):
        if not summary.get(key):
            errors.append(f"{key} is false; this is an engineering pilot")
    for key in ("initialization_substeps", "recorded_substeps"):
        if not summary.get(key):
            errors.append(f"{key} is empty; preload or recorded history is missing")
    for i, (frame, pose) in enumerate(zip(frames, config.get("trajectory", ()))):
        if not math.isclose(
            frame.get("time_s", float("nan")), pose["time_s"], rel_tol=0, abs_tol=1e-10
        ):
            errors.append(f"frame {i} is at t={frame.get('time_s')}, not {pose['time_s']}")
            break
    return errors


def require_dataset(summary, config, what="This run"):
    """Raise unless the run is a complete dataset, naming every reason."""
    errors = dataset_errors(summary, config)
    if errors:
        raise ValueError(f"{what} is not a complete dataset: " + "; ".join(errors))
