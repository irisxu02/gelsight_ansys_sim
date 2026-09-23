"""End-to-end ANSYS solve, surface projection, tactile rendering, and export."""

import json
import time
from dataclasses import replace
from pathlib import Path

from .artifacts import write_json
from .camera import optical_surface
from .config import Config
from .contracts import SurfaceState
from .fem_view import mesh_view
from .mechanics import AnsysGel
from .metrics import validate_frame
from .run_services import RunLifecycle, create_run, prepare_optics, process_frame
from .surface import Markers, image_coordinates, project_surface


def run(
    config,
    output,
    executable=None,
    base_directory=None,
    progress=print,
    resume_directory=None,
    *,
    libraries=None,
    stop_after_s=None,
    numerics_override=False,
    acceptance_override=False,
    from_checkpoint=False,
):

    if config.is_plane:
        from .plane_pipeline import run_plane

        return run_plane(
            config,
            output,
            executable,
            libraries,
            progress,
            stop_after_s,
            base_directory=base_directory,
            resume_directory=resume_directory,
            numerics_override=numerics_override,
            acceptance_override=acceptance_override,
            from_checkpoint=from_checkpoint,
        )
    if stop_after_s is not None:
        raise ValueError("Pilot stopping time currently requires plane geometry")
    if numerics_override:
        raise ValueError("Diagnostic numerics resume currently requires plane geometry")
    if acceptance_override:
        raise ValueError("Acceptance resume currently requires plane geometry")
    config.validate()
    directory = Path(resume_directory) if resume_directory else create_run(output, config)
    progress(f"Run directory: {directory}")
    started = time.perf_counter()
    summary = {
        "schema_version": 1,
        "status": "running",
        "name": config.name,
        "sensor_model": config.sensor_model,
        "calibrated": config.calibrated,
        "mechanics": "ansys",
        "mechanical_configuration": {
            key: config.to_dict()[key]
            for key in ("gel", "material", "indenter", "trajectory", "imported_mesh")
        },
        "material_model": config.material.model,
        "coating_model": "silicone treated as part of the uniform gel; no separate layer stiffness or thickness",
        "surface_geometry": "outer gel solid nodes",
        "render_mode": config.optics.render_mode,
        "optical_model": config.optics.model,
        "indenter_deformable": config.indenter.deformable,
        "contact_model": "finite-sliding augmented-Lagrange Coulomb friction",
        "depth_semantics": "prescribed grip travel from first geometrical touch; deformation divides between object and sensor",
        "frames": [],
        "units": {"length": "m", "force": "N", "pressure": "Pa", "moment": "N m"},
        "time_semantics": (
            "quasi-static load parameter; no viscosity or inertia. Sphere, flat "
            "and imported-mesh runs step through their trajectory as load "
            "parameters, not physical seconds."
        ),
        "gpu_mechanics_requested": config.solver.gpu,
        "gpu_mechanics_verified": False,
    }
    restart = None
    previous_elapsed = 0.0
    if resume_directory:
        saved_config = Config.load(directory / "config.json", validate=False)
        if saved_config.to_dict() != config.to_dict():
            raise ValueError("Resume requires the unchanged saved configuration")
        summary = json.loads((directory / "summary.json").read_text())
        count = len(summary["frames"])
        if summary["status"] == "passed" or not 1 < count < len(config.trajectory):
            raise ValueError(
                "Resume needs an incomplete run with a converged solver state"
            )
        for i, metrics in enumerate(summary["frames"]):
            validate_frame(metrics, config)
            state = SurfaceState.load(directory / "states" / f"frame_{i:04d}.npz")
            if state.load_step != i or state.time_s != config.trajectory[i].time_s:
                raise ValueError("Saved state history does not match the trajectory")
        restart = (count - 1, summary["frames"][-1]["substep"])
        for name in ("gel.rdb", "gel.ldhi", "gel.rst"):
            if not (directory / "solver" / name).is_file():
                raise FileNotFoundError("Required ANSYS restart files are missing")
        previous_elapsed = summary.get("elapsed_s", 0.0)
        summary["status"] = "running"
        summary.pop("error_type", None)
        summary.setdefault("restarts", []).append(
            {"load_step": restart[0], "substep": restart[1]}
        )
    backend = AnsysGel
    if config.indenter.shape == "mesh":
        from .imported_mechanics import AnsysImported

        backend = AnsysImported
    with RunLifecycle(
        directory, summary, started=started, previous_elapsed=previous_elapsed
    ) as lifecycle:
        config, renderer = prepare_optics(config, directory, base_directory)
        summary["render_device"] = renderer.device
        summary["projection_device"] = renderer.device
        with backend(config, directory / "solver", executable, restart=restart) as model:
            view = mesh_view(directory, config, model)
            reference = model.reference_state()
            markers = Markers(
                reference, config.optics.marker_spacing_m, config.camera, config.optics
            )
            completed = len(summary["frames"])
            if completed:
                reference_fields = project_surface(
                    reference, config.camera, backend=config.optics.backend
                )
                reference_fields.update(
                    optical_surface(
                        reference,
                        config.camera,
                        reference_fields,
                        backend=config.optics.backend,
                    )
                )
                rest_pixels = image_coordinates(markers.reference_m, config.camera)
                renderer.render(reference_fields, rest_pixels, rest_pixels)
            for index, pose in enumerate(config.trajectory):
                if index < completed:
                    continue
                if index == 0:
                    state, gpu = reference, {"active": False, "sparse_statistics": []}
                else:
                    summary["phase"] = "solving"
                    write_json(directory / "summary.json", summary)
                    state, gpu = model.solve(pose, index)
                    # A force-controlled pose only carries a placeholder depth;
                    # everything downstream needs the travel the solve reached.
                    pose = model.last_pose
                summary["phase"] = "rendering"
                write_json(directory / "summary.json", summary)
                body_started = time.perf_counter()
                body_metrics = model.save_body_state(
                    directory / "bodies" / f"frame_{index:04d}.npz", pose
                )
                body_elapsed = time.perf_counter() - body_started
                metric = process_frame(
                    directory,
                    index,
                    config,
                    state,
                    markers,
                    renderer,
                    pose,
                    gpu,
                    body_metrics,
                )
                metric["timings"]["save_body_s"] = body_elapsed
                if view is not None:
                    metric["mesh_color_limits"] = view.render(
                        index, state, metric, model.last_displacement
                    )
                if index:
                    metric["timings"].update(model.last_timings)
                summary["frames"].append(metric)
                summary["gpu_mechanics_verified"] |= gpu["active"]
                write_json(directory / "summary.json", summary)
                progress(
                    f"Frame {index + 1}/{len(config.trajectory)}: depth={pose.depth_m * 1000:.3f} mm, force={metric['normal_force_n']:.6f} N, solver GPU={gpu['active']}"
                )
            if view is not None:
                view.close()
        if (
            any(p.depth_m > 0 for p in config.trajectory)
            and max(m["normal_force_n"] for m in summary["frames"]) <= 1e-9
        ):
            raise RuntimeError(
                "Positive indentation produced no compressive contact force"
            )
        lifecycle.finish(config)
    return directory, summary


def rerender(
    source, output, backend=None, progress=print, render_mode=None, config_path=None
):
    """Replay validated states; retain original mechanics provenance and units.

    ``config_path`` replays with that config's optics instead of the run's own;
    its mechanics are held to the run's by the same check as any replay.
    """
    source = Path(source)
    if config_path is not None:
        config_path = Path(config_path)
        config, optics_base = Config.load(config_path), config_path.parent
    else:
        config = Config.load(source / "config.json", validate=False)
        optics_base = source
    if config.is_plane:
        raise ValueError(
            "Plane replay requires its separate unloaded reference; use run --config for a new solve"
        )
    if backend is not None:
        config = replace(config, optics=replace(config.optics, backend=backend))
    if render_mode is not None:
        config = replace(config, optics=replace(config.optics, render_mode=render_mode))
    states = sorted((source / "states").glob("frame_*.npz"))
    original = json.loads((source / "summary.json").read_text())
    mechanical = original.get("mechanical_configuration")
    if mechanical is not None:
        resolved = json.loads(json.dumps(config.to_dict()))
        # Normalize older snapshots through the same backwards-compatible loader;
        # newly added inactive/default fields do not constitute a physics change.
        stored = json.loads(
            json.dumps(Config.from_dict({**resolved, **mechanical}).to_dict())
        )
        if any(resolved[key] != stored[key] for key in mechanical if key != "coating"):
            raise ValueError(
                "Changing materials, coating, geometry, contact, or trajectory requires a new solve"
            )
    if (
        original.get("status") != "passed"
        or len(states) != len(config.trajectory)
        or len(original["frames"]) != len(states)
    ):
        raise ValueError(
            "A complete, validated set of states is required for re-rendering"
        )
    directory = create_run(output, config)
    progress(f"Run directory: {directory}")
    started = time.perf_counter()
    summary = dict(original)
    summary.update(
        status="running",
        mechanics="saved_ansys_states",
        mechanics_run=original.get("mechanics_run", source.name),
        mechanics_elapsed_s=original.get(
            "mechanics_elapsed_s", original.get("elapsed_s")
        ),
        frames=[],
        name=config.name,
        calibrated=config.calibrated,
        render_mode=config.optics.render_mode,
        optical_model=config.optics.model,
    )
    summary.pop("error_type", None)
    summary.pop("report_elapsed_s", None)
    with RunLifecycle(directory, summary, started=started) as lifecycle:
        config, renderer = prepare_optics(config, directory, optics_base)
        import shutil

        if (source / "solid_mesh.npz").is_file():
            shutil.copy2(source / "solid_mesh.npz", directory / "solid_mesh.npz")
            shutil.copytree(source / "bodies", directory / "bodies")
        summary["render_device"] = renderer.device
        summary["projection_device"] = renderer.device
        markers = Markers(
            SurfaceState.load(states[0]),
            config.optics.marker_spacing_m,
            config.camera,
            config.optics,
        )
        for index, path in enumerate(states):
            state = SurfaceState.load(path)
            pose = config.trajectory[index]
            old = original["frames"][index]
            for key in ("time_s", "depth_m", "x_m", "y_m", "twist_rad"):
                if getattr(pose, key) != old[key]:
                    raise ValueError("Trajectory changes require a new mechanics solve")
            metric = process_frame(
                directory,
                index,
                config,
                state,
                markers,
                renderer,
                pose,
                old["gpu_solver"],
                {
                    key: old[key]
                    for key in (
                        "max_indenter_deformation_m",
                        "indenter_grip_displacement_error_m",
                    )
                    if key in old
                },
            )
            summary["frames"].append(metric)
            write_json(directory / "summary.json", summary)
            progress(f"Rendered saved frame {index + 1}/{len(states)}")
        lifecycle.finish(config)
    return directory, summary


def matched_render_scale(config, original):
    """Render a continued run at the scale it was already solved at.

    A --config override states the protocol to carry on with, not the resolution
    to draw it at: the run being continued has a camera, its saved frames were
    rendered through that camera, and the restart rules rightly refuse a resume
    that changes it. Restating the scale on the command line is a trap.

    The scale is the run's own camera against the nominal one its setup declares,
    which is what a resolved plane configuration means by it. Deriving it from
    the two configs instead would read any genuine difference of camera - a
    different sensor, a different field of view - as a scale, and silently let a
    resume past the guard that exists to catch exactly that.
    """
    if not original.is_plane or config.camera == original.camera:
        return config
    nominal = original.specification.suite["sensor"]["camera"]["width_px"]
    scale, remainder = divmod(original.camera.width_px, nominal)
    if remainder:
        raise ValueError(
            f"The run's {original.camera.width_px} px camera is not a whole "
            f"multiple of the {nominal} px its setup declares"
        )
    return config.with_render_scale(scale)


def resume_run(
    source,
    output,
    executable=None,
    progress=print,
    *,
    config=None,
    libraries=None,
    stop_after_s=None,
    numerics_override=False,
    acceptance_override=False,
    from_checkpoint=False,
):
    """Copy an interrupted run and restore its last saved converged load step."""
    import shutil

    source = Path(source)
    original = Config.load(source / "config.json", validate=False)
    config = original if config is None else matched_render_scale(config, original)
    if original.is_plane:
        from .plane_restart import validate_plane_resume

        validate_plane_resume(
            source,
            config,
            numerics_override=numerics_override,
            acceptance_override=acceptance_override,
            from_checkpoint=from_checkpoint,
        )
    elif config.to_dict() != original.to_dict():
        raise ValueError("Non-plane resume requires the unchanged saved configuration")
    directory = create_run(output, config)
    # An interrupted run is the only kind that gets resumed, and an interrupted
    # MAPDL leaves its lock file behind; PyMAPDL refuses to launch over one.
    shutil.copytree(
        source, directory, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock")
    )
    return run(
        config,
        output,
        executable,
        source,
        progress,
        resume_directory=directory,
        libraries=libraries,
        stop_after_s=stop_after_s,
        numerics_override=numerics_override,
        acceptance_override=acceptance_override,
        from_checkpoint=from_checkpoint,
    )
