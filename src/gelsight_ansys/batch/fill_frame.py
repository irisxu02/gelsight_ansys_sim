"""Render frames a plane run solved but never wrote, from its result file.

A checkpoint resume that restarted exactly on a frame time, before the pipeline
learned to render the restored state, left that frame's files missing while
every substep around it is in gel.rst. The report refuses a hole. This reads
the solved substep back, renders the frame the way the run would have, and
rebuilds the report. It needs no solver session: only the result file and the
NMISC offset the run recorded (or was given).
"""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..artifacts import build_report, write_json
from ..config import Config
from ..contracts import SurfaceState
from ..optics import Renderer
from ..plane_coverage import ContactCoverage
from ..plane_mechanics import AnsysPlane, gpu_statistics
from ..plane_pipeline import render_plane_frame, render_unloaded_reference
from ..run_contract import completion_status
from ..surface import Markers


def missing_frame_indices(frames, times, last_solved_s):
    """Frame indices with no recorded metric, among instants the run solved.

    The summary's frame list is what the report is built from, so a frame is
    missing when that list has no entry for its instant - whatever files a
    previous, interrupted attempt may have left on disk. Rendering again is
    idempotent; a metric out of place is not.
    """
    recorded = [f["time_s"] for f in frames]
    return [
        i
        for i, at in enumerate(times)
        if at <= last_solved_s + 1e-9
        and not any(abs(r - at) < 1e-9 for r in recorded)
    ]


def substep_at(summary, at):
    """(load step, substep) of the recorded substep converged at this instant."""
    for record in summary.get("recorded_substeps", []):
        if abs(record["time_s"] - at) < 1e-9:
            return record["load_step"], record["substep"]
    raise ValueError(f"No converged substep was recorded at t = {at:.6g} s")


def insert_frame(frames, metric):
    """Keep frames ordered by time; the report indexes them by position."""
    position = next(
        (i for i, f in enumerate(frames) if f["time_s"] > metric["time_s"] + 1e-9),
        len(frames),
    )
    frames.insert(position, metric)
    return position


def offline_model(config, solver_directory, nonmisc_base):
    """A plane model that reads the result file but never talks to a solver."""
    return AnsysPlane.offline_reader(config, Path(solver_directory), nonmisc_base)


def failed_in_report(summary):
    """Whether the run's only failure was writing its report.

    The lifecycle records the phase a failure happened in. A run that solved
    and checked every frame but could not build its report - a hole in the
    frame sequence - is complete once the hole is filled; a run that failed
    anywhere else is not, and keeps its failure.
    """
    return summary.get("status") == "failed" and summary.get("phase") == "report"


def fill_missing_frames(directory, nonmisc_base=None, progress=print, *, refill=()):
    """Render the frames the run solved but has no record of, and rebuild.

    `refill` names frames to render again: their records are dropped first, so
    they are found missing and rendered by the same path. It is for a frame an
    earlier fill wrote wrongly - one measured against the wrong reference.
    """
    directory = Path(directory)
    config = Config.load(directory / "config.json", validate=False)
    case = config.specification
    summary = json.loads((directory / "summary.json").read_text())
    if refill:
        again = {float(case.frame_times[i]) for i in refill}
        summary["frames"] = [
            f for f in summary["frames"] if not any(abs(f["time_s"] - t) < 1e-9 for t in again)
        ]
        summary["filled_frames"] = [
            f for f in summary.get("filled_frames", []) if f["index"] not in set(refill)
        ]
    recorded = summary.get("recorded_substeps", [])
    if not recorded:
        raise ValueError("The run recorded no converged substeps")
    if nonmisc_base is None:
        nonmisc_base = summary.get("mesh", {}).get("contact_nonmisc_base")
    if nonmisc_base is None:
        raise ValueError(
            "The run did not record its contact NMISC offset; pass --nonmisc-base "
            "(ETYIQR(2,-110) for the run's CONTA174 definition)"
        )
    # A run from before the offset was recorded keeps it from now on.
    summary.setdefault("mesh", {})["contact_nonmisc_base"] = int(nonmisc_base)
    report_failure = failed_in_report(summary)
    times = case.frame_times
    missing = missing_frame_indices(summary["frames"], times, recorded[-1]["time_s"])
    if not missing:
        progress("No frames are missing")
    # The run's saved optical assets - a background image among them - are
    # resolved against its directory, and a fresh renderer has to measure every
    # difference field against the same unloaded rendering the run did.
    renderer = Renderer(config, directory)
    reference = SurfaceState.load(directory / "unloaded_reference.npz")
    markers = Markers(
        reference, config.optics.marker_spacing_m, config.camera, config.optics
    )
    render_unloaded_reference(reference, config, renderer, markers)
    coverage = ContactCoverage(case, reference.reference_m, reference.quads)
    model = offline_model(config, directory / "solver", int(nonmisc_base))
    filled = []
    for index in missing:
        at = float(times[index])
        load_step, substep = substep_at(summary, at)
        state, pose, gpu = next(
            model.converged_states(
                load_step, substep, substep, gpu_statistics(directory / "solver")
            )
        )
        pose = replace(pose, time_s=at)
        state.time_s = at
        check, bins = coverage.evaluate(
            state, model.contact_details, pose, model.object_mesh, model.object_displacement
        )
        metric = render_plane_frame(
            directory, index, config, state, pose, model, markers, renderer, gpu, check, bins
        )
        insert_frame(summary["frames"], metric)
        filled.append({"index": index, "time_s": at, "load_step": load_step, "substep": substep})
        progress(f"Filled frame {index} at t={at:.3f} s from load step {load_step} substep {substep}")
    summary["filled_frames"] = summary.get("filled_frames", []) + filled
    summary["phase"] = "report"
    write_json(directory / "summary.json", summary)
    build_report(directory, config, summary["frames"])
    summary["complete_recorded_interval"] = bool(
        len(summary["frames"]) == len(times)
        and np.isclose(summary["frames"][-1]["time_s"], times[-1], rtol=0, atol=1e-9)
    )
    if report_failure and not missing_frame_indices(
        summary["frames"], times, recorded[-1]["time_s"]
    ):
        # The report was the only thing that failed and it now exists; the run
        # earns the status its mesh, interval and history entitle it to.
        summary["status"] = completion_status(summary)
        summary.pop("error_type", None)
    write_json(directory / "summary.json", summary)
    return filled
