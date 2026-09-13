"""Validate plane restart identity before opening an ANSYS session."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Config
from .contracts import SurfaceState
from .metrics import validate_frame


@dataclass(frozen=True)
class PlaneRestart:
    load_step: int
    substep: int
    frame_index: int
    time_s: float


# Newton-Raphson controls that a restart restates into the resumed database. They
# change how equilibrium is reached, not the model, so a diagnostic resume may
# vary them. Everything else - contact real constants, element formulation,
# geometry - is baked into gel.rdb and needs a fresh run.
NUMERICS_ONLY = frozenset(
    {
        "force_tolerance",
        "force_norm",
        "iterations",
        "nonlinear_diagnostics",
        "transient_points_per_cycle",
        "predict_cutback",
    }
)


def validate_plane_resume(
    directory, config, *, numerics_override=False, acceptance_override=False
):
    directory = Path(directory)
    # The saved configuration is read as a record of the interrupted run, so a
    # validation rule added since it was written must not make it unresumable.
    old = Config.load(directory / "config.json", validate=False)
    config.validate()
    if not old.is_plane or not config.is_plane:
        raise ValueError("Plane restart requires plane configurations")
    a, b = old.to_dict(), config.to_dict()
    changed = {
        key: [a["solver"][key], b["solver"][key]]
        for key in NUMERICS_ONLY
        if a["solver"][key] != b["solver"][key]
    }
    for key in (
        "gel",
        "material",
        "indenter",
        "solver",
        "camera",
        "optics",
        "plane_options",
        "imported_mesh",
    ):
        if a[key] == b[key]:
            continue
        if key == "solver" and numerics_override:
            if {k: v for k, v in a[key].items() if k not in NUMERICS_ONLY} == {
                k: v for k, v in b[key].items() if k not in NUMERICS_ONLY
            }:
                continue
            raise ValueError(
                "A diagnostic resume may only vary "
                + ", ".join(sorted(NUMERICS_ONLY))
                + "; other solver settings need a fresh run"
            )
        extra = (
            ""
            if numerics_override
            else "; pass a diagnostic resume to vary convergence controls"
        )
        if key in ("material", "indenter"):
            extra = (
                "; element formulation and contact real constants are written into "
                "gel.rdb, so changing them requires a fresh run"
            )
        raise ValueError(f"Resume cannot change {key}{extra}")
    if a["specification"]["case"] != b["specification"]["case"]:
        raise ValueError("Resume cannot change specimen material or contact")
    sa, sb = a["specification"]["suite"], b["specification"]["suite"]
    # contact_acceptance is judged on results and never reaches ANSYS, so unlike
    # the mechanical setup it does not have to match what gel.rdb holds. It is
    # still not free: saved frames were validated under the rules in force when
    # they were written, so a change is permitted only when asked for and is
    # recorded against the instant it took effect.
    mechanical = ("protocol", "dataset", "contact_acceptance")
    if {k: v for k, v in sa.items() if k not in mechanical} != {
        k: v for k, v in sb.items() if k not in mechanical
    }:
        raise ValueError("Resume cannot change the mechanical setup")
    acceptance = sa["contact_acceptance"] != sb["contact_acceptance"]
    if acceptance and not acceptance_override:
        raise ValueError(
            "Resume cannot change contact_acceptance; pass an acceptance resume to "
            "vary it, and the frames before the change keep the rules they were "
            "validated under"
        )
    if sa["protocol"]["initialization"] != sb["protocol"]["initialization"]:
        raise ValueError("Resume cannot change preload history")
    summary = json.loads((directory / "summary.json").read_text())
    frames = summary.get("frames", [])
    if not frames or len(frames) >= len(config.trajectory):
        raise ValueError("Resume needs saved states and remaining trajectory")
    last_time = float(frames[-1]["time_s"])
    knots = {
        last_time,
        *(p["time_s"] for p in sa["protocol"]["keyframes"] if p["time_s"] <= last_time),
        *(p["time_s"] for p in sb["protocol"]["keyframes"] if p["time_s"] <= last_time),
    }
    for at in knots:
        if old.physical_pose(at) != config.physical_pose(at):
            raise ValueError("Resume cannot change solved motion history")
        if old.specification.requires_contact(
            at
        ) != config.specification.requires_contact(at):
            raise ValueError("Resume cannot change solved contact requirements")
    for i, metric in enumerate(frames):
        validate_frame(metric, old)
        state = SurfaceState.load(directory / "states" / f"frame_{i:04d}.npz")
        at = config.trajectory[i].time_s
        if not np.isclose(state.time_s, at, rtol=0, atol=1e-10) or not np.isclose(
            metric["time_s"], at, rtol=0, atol=1e-10
        ):
            raise ValueError("Resume sampling must preserve saved frame times")
        indices = np.flatnonzero(
            np.isclose(config.specification.solve_times, at, rtol=0, atol=1e-10)
        )
        if len(indices) != 1 or state.load_step != int(indices[0]) + 1:
            raise ValueError("Resume must preserve saved mechanical checkpoint history")
        if (
            state.source != "ansys"
            or state.load_step != metric["load_step"]
            or state.substep != metric["substep"]
        ):
            raise ValueError("Saved ANSYS state identity does not match its metrics")
        body = directory / "bodies" / f"frame_{i:04d}.npz"
        if not body.is_file():
            raise FileNotFoundError("Missing saved body state for restart verification")
    for name in (
        "unloaded_reference.npz",
        "unloaded_reference.png",
        "solid_mesh.npz",
        "solver/gel.rdb",
        "solver/gel.ldhi",
        "solver/gel.rst",
    ):
        if not (directory / name).is_file():
            raise FileNotFoundError(f"Required plane restart file missing: {name}")
    if not any(
        re.fullmatch(r"gel\.r\d{3}", p.name.lower())
        for p in (directory / "solver").iterdir()
    ):
        raise FileNotFoundError("Missing ANSYS nonlinear restart state (.rnnn)")
    if acceptance:
        # Frames before this instant were never held to the new rules, so the
        # record says where the boundary is rather than implying one standard.
        summary.setdefault("acceptance_overrides", []).append(
            {
                "resumed_at_time_s": last_time,
                "from": sa["contact_acceptance"],
                "to": sb["contact_acceptance"],
            }
        )
    if changed:
        # Frames before and after the change met different convergence criteria,
        # so the sequence is evidence about solver behaviour, not a dataset.
        summary.setdefault("numerics_overrides", []).append(
            {"resumed_at_time_s": last_time, "changed": changed}
        )
        summary["diagnostic_run"] = True
    return PlaneRestart(
        state.load_step, state.substep, len(frames) - 1, last_time
    ), summary
