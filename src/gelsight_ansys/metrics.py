"""Physical consistency checks independent of optical rendering."""

import numpy as np


def frame_metrics(state, fields, marker_reference, marker_position, pose, gpu):
    for name, value in state.__dict__.items():
        if isinstance(value, np.ndarray) and not np.isfinite(value).all():
            raise ValueError(f"Nonfinite solver output in {name}")
    force = state.contact_force_n.sum(axis=0)
    moment = (
        np.cross(state.position_m, state.contact_force_n) + state.contact_couple_nm
    ).sum(axis=0)
    pilot_moment = state.pilot_moment_nm + np.cross(
        state.pilot_position_m, state.pilot_reaction_n
    )
    normal_load = -state.contact_force_n[:, 2]
    center = (
        (state.position_m[:, :2] * normal_load[:, None]).sum(axis=0) / normal_load.sum()
        if normal_load.sum() > 1e-10
        else None
    )
    slip = state.contact_elastic_slip_m
    status = state.contact_integration_status
    sticking = slip[np.rint(status) == 3] if slip.size else np.array([])
    return {
        "max_elastic_slip_m": float(slip.max()) if slip.size else None,
        "max_sticking_elastic_slip_m": float(sticking.max()) if sticking.size else None,
        "sticking_contact_points": int(np.count_nonzero(np.rint(status) == 3)),
        "sliding_contact_points": int(np.count_nonzero(np.rint(status) == 2)),
        "time_s": float(pose.time_s),
        "depth_m": float(pose.depth_m),
        "x_m": float(pose.x_m),
        "y_m": float(pose.y_m),
        "twist_rad": float(pose.twist_rad),
        "state_source": state.source,
        "load_step": state.load_step,
        "substep": state.substep,
        "force_on_gel_n": force.tolist(),
        "normal_force_n": float(-force[2]),
        "moment_on_gel_nm": moment.tolist(),
        "backing_reaction_n": state.backing_reaction_n.tolist(),
        "pilot_reaction_n": state.pilot_reaction_n.tolist(),
        "force_balance_error_n": float(
            np.linalg.norm(force + state.backing_reaction_n)
        ),
        "pilot_force_error_n": float(np.linalg.norm(force - state.pilot_reaction_n)),
        # Not observable through a coupled degree of freedom; see platen_load.
        "pilot_moment_error_nm": (
            None
            if pose.force_controlled
            else float(np.linalg.norm(moment - pilot_moment))
        ),
        # Load control drives the platen through a coupled degree of freedom, so
        # its normal load has no per-node reaction and the force check becomes
        # "the solver delivered what was commanded" rather than a free residual.
        "platen_control": "load" if pose.force_controlled else "travel",
        "commanded_normal_force_n": (
            float(pose.normal_force_n) if pose.force_controlled else None
        ),
        "center_of_pressure_m": center.tolist() if center is not None else None,
        "fov_force_n": fields["fov_force_n"].tolist(),
        "off_fov_force_n": (force - fields["fov_force_n"]).tolist(),
        "raster_force_error_n": float(np.linalg.norm(fields["raster_force_error_n"])),
        "max_pressure_pa": float(state.contact_pressure_pa.max()),
        "max_penetration_m": float(max(0.0, state.contact_penetration_m.max())),
        "closed_contact_elements": int(np.count_nonzero(state.contact_status >= 2)),
        "sliding_contact_elements": int(
            np.count_nonzero(np.rint(state.contact_status) == 2)
        ),
        "max_marker_displacement_m": float(
            np.linalg.norm(marker_position - marker_reference, axis=1).max()
        ),
        "max_marker_in_plane_displacement_m": float(
            np.linalg.norm((marker_position - marker_reference)[:, :2], axis=1).max()
        ),
        "max_surface_displacement_m": float(
            np.linalg.norm(state.displacement_m, axis=1).max()
        ),
        "gpu_solver": gpu,
    }


def validate_frame(metrics, config):
    magnitude = np.linalg.norm(metrics["force_on_gel_n"])
    tolerance = max(config.solver.balance_tolerance * magnitude, 1e-6)
    if metrics["force_balance_error_n"] > tolerance:
        raise RuntimeError(
            "Contact and backing forces fail the configured balance tolerance"
        )
    if metrics["pilot_force_error_n"] > tolerance:
        raise RuntimeError(
            "Contact and pilot forces fail the configured balance tolerance"
        )
    if metrics["raster_force_error_n"] > max(1e-8 * magnitude, 1e-10):
        raise RuntimeError("Surface-to-image force conservation failed")
