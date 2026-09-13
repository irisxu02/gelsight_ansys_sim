"""One public command for geometry/material-configured contact simulations."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path


def configure_run(config, args):
    """Apply common CLI options, rejecting controls unsupported by an adapter."""

    from .simulation_config import ConfigurationError

    plane = config.is_plane
    if plane and args.refine_contact:
        raise ConfigurationError(
            "For plane geometry, select uniform or refined with discretization.gel_mesh in the setup"
        )
    plane_options = (
        args.object_mesh,
        args.object_element_size_m,
        args.pilot_element_size_m,
        args.stop_after_s,
        args.solver_mode,
        args.libraries,
        args.sample_interval_s,
        args.solve_interval_s,
        args.maximum_time_increment_s,
    )
    if not plane and any(value is not None for value in plane_options):
        raise ConfigurationError(
            "Object mesh, pilot, solver-mode, and library options currently require plane geometry"
        )
    if plane:
        if args.solver_mode:
            from .simulation_config import plane_solver

            config = replace(
                config, solver=plane_solver(config.specification, args.solver_mode)
            )
        config = config.with_plane_options(
            object_mesh=args.object_mesh or config.plane_options.object_mesh,
            object_element_size_m=(
                args.object_element_size_m
                if args.object_element_size_m is not None
                else config.plane_options.object_element_size_m
            ),
            element_size_m=args.pilot_element_size_m,
        )
        if any(
            value is not None
            for value in (
                args.sample_interval_s,
                args.solve_interval_s,
                args.maximum_time_increment_s,
            )
        ):
            config = config.with_plane_sampling(
                sample_interval_s=args.sample_interval_s,
                solve_interval_s=args.solve_interval_s,
                maximum_time_increment_s=args.maximum_time_increment_s,
            )
    elif args.refine_contact:
        config = config.with_contact_refinement()
    solver = {}
    if args.equation_solver:
        solver["equation_solver"] = args.equation_solver
    if args.force_tolerance is not None:
        solver["force_tolerance"] = args.force_tolerance
    if args.force_norm is not None:
        solver["force_norm"] = args.force_norm
    if args.diagnose:
        solver["nonlinear_diagnostics"] = True
    if args.allow_unlisted_gpu:
        solver["allow_unlisted_gpu"] = True
    if args.solver_gpu:
        solver.update(cores=3, gpu=True, require_gpu=True)
    elif args.solver_cpu or args.cpu:
        solver.update(cores=4, gpu=False, require_gpu=False)
    optics = {}
    if args.cpu:
        optics["backend"] = "cpu"
    if args.subtract_background:
        optics["render_mode"] = "subtracted"
    config = config.with_solver(**solver).with_optics(**optics)
    if args.gel_formulation:
        config = config.with_gel_material(formulation=args.gel_formulation)
    damping = {}
    if args.contact_damping_normal is not None:
        damping["stabilization_damping_normal"] = args.contact_damping_normal
    if args.contact_damping_tangential is not None:
        damping["stabilization_damping_tangential"] = args.contact_damping_tangential
    if args.contact_damping_activation:
        damping["stabilization_damping_activation"] = args.contact_damping_activation
    if damping:
        if not plane:
            raise ConfigurationError(
                "Contact stabilization damping currently requires plane geometry"
            )
        config = config.with_contact_damping(**damping)
    return config.with_render_scale(args.render_scale).validate()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ANSYS GelSight contact and tactile rendering"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    solve = commands.add_parser(
        "run", help="Solve the geometry, material, and motion selected in a config"
    )
    solve.add_argument("--config", type=Path, required=True)
    solve.add_argument("--output", type=Path, default=Path("outputs"))
    solve.add_argument("--exec-file", type=Path)
    solve.add_argument("--render-scale", type=int, default=1)
    solve.add_argument(
        "--libraries", type=Path, help="Native material/contact adapter directory"
    )
    solve.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and check configuration without launching ANSYS",
    )
    solve.add_argument("--equation-solver", choices=("sparse", "mixed"))
    solve.add_argument(
        "--force-tolerance",
        type=float,
        help="CNVTOL force tolerance; overrides the setup's declared value",
    )
    solve.add_argument(
        "--force-norm",
        type=int,
        choices=(1, 2),
        help="CNVTOL residual norm: 1 (L1) or 2 (L2)",
    )
    solve.add_argument(
        "--gel-formulation",
        choices=("displacement", "mixed_up"),
        help="Sensor gel element formulation; mixed_up suits near-incompressible gel",
    )
    solve.add_argument(
        "--contact-damping-normal",
        type=float,
        help="CONTA174 FDMN stabilization damping factor for near-field points",
    )
    solve.add_argument(
        "--contact-damping-tangential",
        type=float,
        help="CONTA174 FDMT stabilization damping factor for near-field points",
    )
    solve.add_argument(
        "--contact-damping-activation",
        choices=("first_load_step", "all_load_steps", "always"),
        help="CONTA174 KEYOPT(15); always keeps damping on points that reopen",
    )
    solve.add_argument(
        "--diagnose",
        action="store_true",
        help="Write NLDIAG residual and contact tracking files naming failing elements",
    )
    solve.add_argument(
        "--refine-contact",
        action="store_true",
        help="Apply local contact refinement for sphere/flat/mesh geometry",
    )
    solve.add_argument(
        "--object-mesh",
        choices=("simplified", "matched"),
        help="Plane-object mesh selection",
    )
    solve.add_argument(
        "--object-element-size-m",
        type=float,
        help="Independent deformable slab cell size",
    )
    solve.add_argument(
        "--pilot-element-size-m",
        type=float,
        help="Coarse plane engineering probe; cannot replace a production example",
    )
    solve.add_argument(
        "--sample-interval-s",
        type=float,
        help="Plane saved-frame interval; preserves the mechanical schedule unless changed explicitly",
    )
    solve.add_argument(
        "--solve-interval-s",
        type=float,
        help="Plane mechanical checkpoint interval; saved interval must be an integer multiple",
    )
    solve.add_argument(
        "--maximum-time-increment-s",
        type=float,
        help="Upper limit on adaptive internal plane time steps",
    )
    solve.add_argument(
        "--stop-after-s",
        type=float,
        help="Stop a plane engineering probe at a recorded physical time",
    )
    solve.add_argument(
        "--allow-unlisted-gpu",
        action="store_true",
        help="Apply the ANSYS device override for this process",
    )
    allocation = solve.add_mutually_exclusive_group()
    allocation.add_argument(
        "--solver-gpu",
        action="store_true",
        help="Three CPU solver cores plus solver GPU; CUDA optics",
    )
    allocation.add_argument(
        "--solver-cpu",
        action="store_true",
        help="Four CPU solver cores; configured optics (default)",
    )
    allocation.add_argument(
        "--cpu", action="store_true", help="CPU mechanics, projection, and rendering"
    )
    allocation.add_argument(
        "--solver-mode",
        choices=("cpu", "specified"),
        help="Plane solver allocation: default CPU or as declared in its setup",
    )
    resume = commands.add_parser(
        "resume", help="Continue from copied ANSYS restart files"
    )
    resume.add_argument("--run", type=Path, required=True)
    resume.add_argument("--output", type=Path, default=Path("outputs"))
    resume.add_argument(
        "--config", type=Path, help="Optional plane config with unchanged solved history"
    )
    resume.add_argument("--exec-file", type=Path)
    resume.add_argument("--libraries", type=Path)
    resume.add_argument("--stop-after-s", type=float)
    resume.add_argument(
        "--force-tolerance",
        type=float,
        help="Restate CNVTOL on the resumed model; requires --diagnostic-numerics",
    )
    resume.add_argument(
        "--force-norm",
        type=int,
        choices=(1, 2),
        help="Restate the CNVTOL norm; requires --diagnostic-numerics",
    )
    resume.add_argument(
        "--diagnose",
        action="store_true",
        help="Write NLDIAG files on the resumed model; requires --diagnostic-numerics",
    )
    resume.add_argument(
        "--from-checkpoint",
        action="store_true",
        help="Continue from the solver's last converged load step instead of the last "
        "saved frame. MAPDL always keeps that one restart point; substeps it converged "
        "after the last checked one are replayed from gel.rst through the same checks.",
    )
    resume.add_argument(
        "--acceptance-override",
        action="store_true",
        help="Permit contact_acceptance to change across the restart. The criterion "
        "is judged on results and never reaches ANSYS, so gel.rdb does not "
        "constrain it, but frames saved before the restart were validated under "
        "the old rules and keep them; the summary records where the boundary is.",
    )
    resume.add_argument(
        "--diagnostic-numerics",
        action="store_true",
        help="Permit convergence-control changes across the restart. Frames before "
        "and after met different criteria, so the result is marked diagnostic and "
        "is not a dataset. Contact real constants and element formulation are held "
        "in gel.rdb and still require a fresh run.",
    )
    render = commands.add_parser(
        "render", help="Re-render saved supported states without launching ANSYS"
    )
    render.add_argument("--run", type=Path, required=True)
    render.add_argument("--output", type=Path, default=Path("outputs"))
    render.add_argument("--backend", choices=("cpu", "cuda"))
    for command in (solve, render):
        command.add_argument(
            "--subtract-background",
            action="store_true",
            help="Display signed RGB difference from the unloaded reference",
        )
    args = parser.parse_args(argv)
    from .simulation_config import ConfigurationError

    try:
        from .config import Config
        from .pipeline import rerender, resume_run, run

        if args.command == "run":
            config = configure_run(Config.load(args.config), args)
            if args.dry_run:
                plane = config.is_plane
                shape = "plane" if plane else config.indenter.shape
                material = (
                    config.specification.bulk["model"]
                    if plane
                    else (
                        config.indenter.material.model
                        if config.indenter.deformable
                        else "rigid"
                    )
                )
                print(
                    f"Configuration checked: {config.name}; object={shape}; material={material}; frames={len(config.trajectory)}; optics={config.optics.backend}"
                )
                return 0
            directory, summary = run(
                config,
                args.output,
                args.exec_file,
                args.config.parent,
                libraries=args.libraries,
                stop_after_s=args.stop_after_s,
            )
        elif args.command == "resume":
            config = Config.load(args.config) if args.config else None
            overrides = {}
            if args.force_tolerance is not None:
                overrides["force_tolerance"] = args.force_tolerance
            if args.force_norm is not None:
                overrides["force_norm"] = args.force_norm
            if args.diagnose:
                overrides["nonlinear_diagnostics"] = True
            if overrides:
                if not args.diagnostic_numerics:
                    raise ConfigurationError(
                        "Changing convergence controls across a restart needs "
                        "--diagnostic-numerics; the result is marked diagnostic"
                    )
                if config is None:
                    config = Config.load(args.run / "config.json", validate=False)
                config = config.with_solver(**overrides)
            directory, summary = resume_run(
                args.run,
                args.output,
                args.exec_file,
                config=config,
                libraries=args.libraries,
                stop_after_s=args.stop_after_s,
                numerics_override=args.diagnostic_numerics,
                acceptance_override=args.acceptance_override,
                from_checkpoint=args.from_checkpoint,
            )
        else:
            directory, summary = rerender(
                args.run,
                args.output,
                args.backend,
                render_mode="subtracted" if args.subtract_background else None,
            )
        print(
            f"{summary['status']}: {len(summary['frames'])} frames. Results: {directory}"
        )
        return 0
    except ConfigurationError as error:
        print(f"Configuration rejected: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(
            f"Simulation failed ({type(error).__name__}). Review configuration and the run summary/private logs.",
            file=sys.stderr,
        )
        return 1
