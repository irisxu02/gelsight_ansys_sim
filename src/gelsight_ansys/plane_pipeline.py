"""Recorded plane trajectories with physical-time and full-contact acceptance."""

import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from PIL import Image

from .artifacts import write_json
from .camera import optical_surface
from .config import Camera
from .plane_coverage import ContactCoverage
from .plane_mechanics import AnsysPlane
from .run_services import RunLifecycle, create_run, prepare_optics, process_frame
from .surface import Markers, image_coordinates


def render_plane_frame(
    directory,
    index,
    config,
    state,
    pose,
    model,
    markers,
    renderer,
    gpu,
    coverage,
    bin_force,
):
    started = time.perf_counter()
    # Store force fields on the nominal grid; render optical rays at full resolution.
    body = model.save_body_state(directory / "bodies" / f"frame_{index:04d}.npz", pose)
    metric = process_frame(
        directory,
        index,
        config,
        state,
        markers,
        renderer,
        pose,
        gpu,
        body,
        field_camera=Camera(**config.specification.suite["sensor"]["camera"]),
        store_optical_fields=False,
        extra_fields={"macroscopic_bin_repulsive_force_n": bin_force},
        extra_metrics={"contact_coverage": coverage},
    )
    (directory / "contact").mkdir(exist_ok=True)
    np.savez_compressed(
        directory / "contact" / f"frame_{index:04d}.npz", **model.contact_details
    )
    metric["timings"] = {
        **metric["timings"],
        **model.last_timings,
        "render_and_export_s": time.perf_counter() - started,
    }
    return metric


def run_plane(
    config,
    output,
    executable=None,
    libraries=None,
    progress=print,
    stop_after_s=None,
    *,
    base_directory=None,
    resume_directory=None,
    numerics_override=False,
    acceptance_override=False,
    from_checkpoint=False,
):
    config.validate()
    case = config.specification
    if config.camera.projection != "pinhole":
        raise ValueError(
            "Plane multi-resolution exports currently require the specified pinhole camera"
        )
    finish = float(case.frame_times[-1] if stop_after_s is None else stop_after_s)
    if finish < 0 or finish > case.frame_times[-1]:
        raise ValueError("Pilot stopping time must be within the recorded interval")
    times = case.frame_times[case.frame_times <= finish + 1e-12]
    solve_times = case.solve_times[case.solve_times <= times[-1] + 1e-12]
    frame_indices = {round(float(at), 12): i for i, at in enumerate(times)}
    directory = Path(resume_directory) if resume_directory else create_run(output, config)
    progress(f"Run directory: {directory}")
    started = time.perf_counter()
    summary = {
        "schema_version": 2,
        "name": config.name,
        "status": "running",
        "mechanics": "ansys",
        "frames": [],
        "sensor_model": config.sensor_model,
        "calibrated": False,
        "bulk_material": case.bulk,
        "contact_model": case.case["contact"],
        "coating_model": "silicone homogenized into the uniform gel; no separate mechanical film",
        "time_semantics": "physical seconds; quasi-static with Prony relaxation, no inertia; initialization times are recorded in the resolved config and retain material/contact history",
        "depth_semantics": "total platen travel from first touch, divided between gel and specimen",
        "gpu_mechanics_requested": config.solver.gpu,
        "gpu_mechanics_verified": False,
        "production_mesh": config.plane_options.element_size_m is None,
        "complete_recorded_interval": bool(finish == case.frame_times[-1]),
        "field_grid_width_height_px": [
            case.suite["sensor"]["camera"]["width_px"],
            case.suite["sensor"]["camera"]["height_px"],
        ],
        "optical_grid_width_height_px": [
            config.camera.width_px,
            config.camera.height_px,
        ],
        "stored_optical_fields": False,
        "mechanical_checkpoints": len(solve_times),
        "solve_interval_s": case.suite["dataset"].get(
            "solve_interval_s", case.suite["dataset"]["sample_interval_s"]
        ),
        "sample_interval_s": case.suite["dataset"]["sample_interval_s"],
        "optical_reconstruction": "regenerate exact camera-ray geometry from saved surface states and camera.json",
        "units": {
            "length": "m",
            "force": "N",
            "pressure": "Pa",
            "moment": "N m",
            "time": "s",
        },
        "initialization_substeps": [],
        "recorded_substeps": [],
    }
    restart = None
    previous_elapsed = 0.0
    if resume_directory:
        from .plane_restart import validate_plane_resume

        restart, saved = validate_plane_resume(
            directory,
            config,
            numerics_override=numerics_override,
            acceptance_override=acceptance_override,
            from_checkpoint=from_checkpoint,
        )
        previous_elapsed = saved.get("elapsed_s", 0.0)
        saved.update(
            {
                key: summary[key]
                for key in (
                    "name",
                    "complete_recorded_interval",
                    "mechanical_checkpoints",
                    "solve_interval_s",
                    "sample_interval_s",
                )
            }
        )
        summary = saved
        summary.pop("error_type", None)
        summary.setdefault("restarts", []).append(asdict(restart))
        solve_times = solve_times[solve_times > restart.time_s + 1e-12]
    with RunLifecycle(
        directory, summary, started=started, previous_elapsed=previous_elapsed
    ) as lifecycle:
        config, renderer = prepare_optics(config, directory, base_directory)
        summary["render_device"] = summary["projection_device"] = renderer.device
        resume_options = {"restart": restart} if restart else {}
        with AnsysPlane(
            config, directory / "solver", executable, libraries, **resume_options
        ) as model:
            if model.native_manifest:
                summary["native_adapters"] = model.native_manifest
            if restart:
                from .contracts import SurfaceState

                reference = SurfaceState.load(directory / "unloaded_reference.npz")
            else:
                reference = model.reference_state()
                reference.time_s = case.suite["protocol"]["initialization"][
                    "start_time_s"
                ]
                reference.save(directory / "unloaded_reference.npz")
            markers = Markers(
                reference, config.optics.marker_spacing_m, config.camera, config.optics
            )
            rest_pixels = image_coordinates(markers.reference_m, config.camera)
            optical = optical_surface(
                reference, config.camera, {}, backend=config.optics.backend
            )
            # Renderer only uses optical_* when these are supplied.
            baseline_fields = {
                **optical,
                "normals": optical["optical_normals"],
                "valid_mask": optical["optical_valid_mask"],
                "position_m": optical["optical_position_m"],
                "displacement_m": np.zeros_like(optical["optical_position_m"]),
            }
            baseline = renderer.render(baseline_fields, rest_pixels, rest_pixels)
            Image.fromarray(baseline).save(directory / "unloaded_reference.png")
            coverage = ContactCoverage(case, reference.reference_m, reference.quads)
            summary["mesh"] = {
                "gel_nodes": len(model.mesh.coordinates),
                "gel_hexes": len(model.mesh.hexes),
                "contact_faces": len(model.mesh.surface_quads),
                "specimen_nodes": len(model.object_mesh.coordinates)
                if model.object_mesh is not None
                else 0,
                "specimen_hexes": len(model.object_mesh.hexes)
                if model.object_mesh is not None
                else 0,
            }
            write_json(directory / "summary.json", summary)
            def record_substep(state, pose, gpu):
                """Check one converged substep and keep its record."""
                check, bins = coverage.evaluate(
                    state,
                    model.contact_details,
                    pose,
                    model.object_mesh,
                    model.object_displacement,
                )
                force = state.contact_force_n.sum(axis=0)
                record = {
                    "time_s": state.time_s,
                    "load_step": state.load_step,
                    "substep": state.substep,
                    "normal_force_n": float(-force[2]),
                    "contact_coverage": check,
                }
                if state.time_s >= -1e-10:
                    if case.requires_contact(state.time_s):
                        coverage.validate(check)
                    else:
                        coverage.validate(check, require_contact=False)
                    record["contact_required"] = case.requires_contact(state.time_s)
                    error = float(np.linalg.norm(force + state.backing_reaction_n))
                    record["force_balance_error_n"] = error
                    window = case.transient_at(state.time_s)
                    if window is not None and window.get("inertia", True):
                        # With mass integrated, contact minus backing is the
                        # gel's inertial force, not an error. It is recorded,
                        # and the quasi-static balance is demanded again on
                        # the first substep after the window - which also
                        # checks that inertia was not switched off while
                        # kinetic energy remained.
                        record["balance_check"] = "inertial_window"
                    elif error > max(
                        config.solver.balance_tolerance * np.linalg.norm(force),
                        1e-6,
                    ):
                        raise RuntimeError(
                            "A recorded plane substep failed force balance"
                        )
                    summary["recorded_substeps"].append(record)
                else:
                    summary["initialization_substeps"].append(record)
                if state.substep == 1:
                    write_json(directory / "summary.json", summary)
                return state, pose, gpu, check, bins

            if restart and restart.replay_from_substep:
                # Substeps the solver converged past the last one that was
                # checked, replayed under the same rules before solving on.
                summary["phase"] = "replaying"
                write_json(directory / "summary.json", summary)
                for state, pose, gpu in model.replay_load_step(restart):
                    record_substep(state, pose, gpu)
                write_json(directory / "summary.json", summary)
            def save_frame(at, index, last):
                """Render and record the frame at a solved instant."""
                state, pose, gpu, check, bins = last
                pose = replace(pose, time_s=float(at))
                state.time_s = float(at)
                summary["phase"] = "rendering"
                write_json(directory / "summary.json", summary)
                metric = render_plane_frame(
                    directory,
                    index,
                    config,
                    state,
                    pose,
                    model,
                    markers,
                    renderer,
                    gpu,
                    check,
                    bins,
                )
                summary["frames"].append(metric)
                summary["gpu_mechanics_verified"] |= gpu["active"]
                summary["elapsed_s"] = previous_elapsed + time.perf_counter() - started
                write_json(directory / "summary.json", summary)
                progress(
                    f"Frame {index + 1}/{len(times)}: physical t={at:.2f} s, force={metric['normal_force_n']:.6f} N, active bins={check['active_bin_fraction']:.3f}"
                )

            if restart:
                # The restart point can be a frame time whose frame was never
                # written - the run stopped after solving it. Render it now
                # from the restored state, so the sequence has no hole.
                index = frame_indices.get(round(float(restart.time_s), 12))
                if index is not None and index >= len(summary["frames"]):
                    state, pose, gpu = model.restored()
                    check, bins = coverage.evaluate(
                        state,
                        model.contact_details,
                        pose,
                        model.object_mesh,
                        model.object_displacement,
                    )
                    save_frame(float(restart.time_s), index, (state, pose, gpu, check, bins))
            for at in solve_times:
                target = config.physical_pose(float(at))
                last = None
                summary["phase"] = "solving"
                summary["target_time_s"] = float(at)
                write_json(directory / "summary.json", summary)
                for state, pose, gpu in model.solve_interval(target):
                    last = record_substep(state, pose, gpu)
                if last is None or not np.isclose(last[0].time_s, at, rtol=0, atol=1e-9):
                    raise RuntimeError("Missing requested physical-time frame")
                summary["elapsed_s"] = previous_elapsed + time.perf_counter() - started
                write_json(directory / "summary.json", summary)
                index = frame_indices.get(round(float(at), 12))
                if index is None:
                    continue
                save_frame(float(at), index, last)
        if (
            case.suite["protocol"].get("release", False)
            and summary["complete_recorded_interval"]
        ):
            final = summary["frames"][-1]
            if np.linalg.norm(final["force_on_gel_n"]) > 1e-6:
                raise RuntimeError("Plane release left residual contact force")
            if final["max_surface_displacement_m"] > 1e-8:
                raise RuntimeError("Elastic gel did not recover after plane release")
            summary["release_verified"] = True
        lifecycle.finish(
            config,
            "diagnostic_passed"
            if summary.get("diagnostic_run")
            else "passed"
            if summary["production_mesh"] and summary["complete_recorded_interval"]
            else "pilot_passed",
        )
    return directory, summary
