"""Render frames a plane run solved but never wrote, from its result file.

A checkpoint resume that restarted exactly on a frame time, before the pipeline
learned to render the restored state, left that frame's files missing while
every substep around it is in gel.rst. The report refuses a hole. This reads
the solved substep back, renders the frame the way the run would have, and
rebuilds the report. It needs no solver session: only the result file and the
NMISC offset the run recorded (or was given).
"""

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from ..artifacts import build_report, write_json
from ..config import Config
from ..contracts import SurfaceState
from ..plane_coverage import ContactCoverage
from ..plane_mechanics import AnsysPlane, gpu_statistics
from ..plane_pipeline import render_plane_frame
from ..run_services import prepare_optics
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
    with tempfile.TemporaryDirectory() as scratch:
        model = AnsysPlane(config, Path(scratch) / "solver")
        model.mapdl = MagicMock()
        model.mapdl.get_value.return_value = 0
        try:
            model.build()  # writes the deck it would send, and sets the model up
        except RuntimeError as error:
            if "node import failed" not in str(error):
                raise
    model.directory = Path(solver_directory)
    model.nonmisc_base = nonmisc_base
    # Solve timings belong to a solver session; a filled frame has none.
    model.last_timings = {}
    return model


def fill_missing_frames(directory, nonmisc_base=None, progress=print):
    directory = Path(directory)
    config = Config.load(directory / "config.json", validate=False)
    case = config.specification
    summary = json.loads((directory / "summary.json").read_text())
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
    times = case.frame_times
    missing = missing_frame_indices(summary["frames"], times, recorded[-1]["time_s"])
    if not missing:
        progress("No frames are missing")
    config, renderer = prepare_optics(config, directory, None)
    reference = SurfaceState.load(directory / "unloaded_reference.npz")
    markers = Markers(
        reference, config.optics.marker_spacing_m, config.camera, config.optics
    )
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
    if summary.get("status") == "failed" and summary.get("error_type") == "FileNotFoundError":
        summary["status"] = "passed"
        summary.pop("error_type", None)
    summary["complete_recorded_interval"] = bool(
        np.isclose(summary["frames"][-1]["time_s"], times[-1], rtol=0, atol=1e-9)
    )
    write_json(directory / "summary.json", summary)
    return filled
