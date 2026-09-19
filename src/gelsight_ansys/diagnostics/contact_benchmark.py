"""Short licensed contact diagnostics; never replace published examples."""

import argparse
import json
import subprocess
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..artifacts import write_json
from ..config import Config
from ..imported_mechanics import AnsysImported
from ..mechanics import AnsysGel
from ..plane_mechanics import AnsysPlane
from ..simulation_config import config_for_plane

ROOT = Path(__file__).resolve().parents[3]


def stop_worker(child, solver_directory):
    """Stop one diagnostic client and its solver, including detached MAPDL launches."""
    import psutil

    owned = {}
    try:
        process = psutil.Process(child.pid)
        owned[process.pid] = process
        for descendant in process.children(recursive=True):
            owned[descendant.pid] = descendant
    except psutil.NoSuchProcess:
        pass
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").lower().startswith("ansys") and Path(
                process.cwd()
            ).resolve() == solver_directory.resolve():
                owned[process.pid] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for process in owned.values():
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(list(owned.values()), timeout=10)
    if alive:
        raise RuntimeError("Diagnostic processes did not stop; refusing another checkout")


def supervise(args):
    """A separate parent enforces time limits even if a MAPDL RPC stops responding."""
    args.output.mkdir(parents=True, exist_ok=False)
    results = {}
    for variant in args.variants:
        results[variant] = {
            "status": "running",
            "variant": variant,
            "states": [],
            "diagnostic_only": True,
        }
        write_json(args.output / "benchmark.json", results)
        command = [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            "--worker",
            "--config",
            str(args.config.resolve()),
            "--output",
            str(args.output.resolve()),
            "--variants",
            variant,
            "--stop-time",
            str(args.stop_time),
            "--dt",
            str(args.dt),
        ]
        if args.object_size is not None:
            command.extend(["--object-size", str(args.object_size)])
        if args.exec_file:
            command.extend(["--exec-file", str(args.exec_file.resolve())])
        started = time.perf_counter()
        timeout = False
        with (args.output / f"worker_{variant}.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            )
            try:
                child.wait(timeout=args.timeout_s)
            except subprocess.TimeoutExpired:
                timeout = True
                stop_worker(child, args.output / variant / "solver")
            except BaseException:
                stop_worker(child, args.output / variant / "solver")
                raise
        path = args.output / f"worker_{variant}.json"
        if path.exists():
            results[variant] = json.loads(path.read_text())[variant]
        record = results[variant]
        if timeout or record["status"] == "running" or child.returncode:
            record.update(
                status="failed",
                error_type="TimeoutError" if timeout else "WorkerError",
                error=f"Diagnostic exceeded {args.timeout_s:g} seconds"
                if timeout
                else "Diagnostic worker exited without a completed record",
                elapsed_s=time.perf_counter() - started,
            )
        for startup in (args.output / variant / "solver").glob("*.out"):
            if "ANSYS LICENSE MANAGER ERROR" in startup.read_text(errors="replace"):
                record.update(
                    status="blocked",
                    error_type="LicenseUnavailable",
                    error="ANSYS license checkout failed; inspect the private solver startup log.",
                )
                break
        write_json(args.output / "benchmark.json", results)
        print(json.dumps({k: v for k, v in record.items() if k != "states"}), flush=True)
        if record["status"] == "blocked":
            return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/material_plane_slide/rigid_reference.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--variants", nargs="+", default=["baseline", "mixed_up", "projection", "softer"]
    )
    parser.add_argument("--stop-time", type=float, default=-1.7)
    parser.add_argument("--dt", type=float, default=0.01)
    parser.add_argument("--object-size", type=float)
    parser.add_argument("--exec-file", type=Path)
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=600,
        help="Wall-time limit per diagnostic; stops only its owned solver",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    allowed = {
        "baseline",
        "mixed_up",
        "projection",
        "softer",
        "no_predictor",
        "mixed_projection",
        "unified",
    }
    if set(args.variants) - allowed:
        parser.error("Unknown benchmark variant")
    if "no_predictor" in args.variants and Config.load(args.config).is_plane:
        # The plane adapter always solves with PRED,OFF, so the variant would
        # only repeat the baseline under another name.
        parser.error(
            "no_predictor is a variant of the sphere adapter; the plane baseline "
            "already runs with the predictor off"
        )
    if args.timeout_s <= 0:
        parser.error("timeout-s must be positive")
    if not args.worker:
        return supervise(args)
    if len(args.variants) != 1:
        parser.error("Worker must run exactly one variant")
    args.output.mkdir(parents=True, exist_ok=True)
    result_file = args.output / f"worker_{args.variants[0]}.json"
    results = {}
    for variant in args.variants:
        config = Config.load(args.config)
        if config.is_plane:
            case = config.specification
            case.suite["solver"]["maximum_time_increment_s"] = args.dt
            if variant in ("mixed_up", "mixed_projection"):
                case.suite["sensor"]["material"]["formulation"] = "mixed_up"
            config = config_for_plane(case, object_element_size_m=args.object_size)
        elif variant in ("mixed_up", "mixed_projection"):
            config = replace(
                config, material=replace(config.material, formulation="mixed_up")
            )
        config = replace(config, name=f"contact_benchmark_{variant}").validate()
        if (
            variant in ("projection", "mixed_projection")
            and config.indenter.shape == "sphere"
            and not config.indenter.deformable
        ):
            raise ValueError(
                "Surface projection requires a faceted target, not a primitive sphere"
            )
        directory = args.output / variant
        directory.mkdir()
        write_json(directory / "config.json", config.to_dict())
        write_json(
            directory / "diagnostic_controls.json",
            {
                "contact_detection_keyopt4": 5
                if variant == "unified"
                else 3
                if variant in ("projection", "mixed_projection")
                else 0,
                "normal_stiffness_factor": 0.1
                if variant == "softer"
                else config.indenter.stiffness_factor,
                "predictor_off_override": variant == "no_predictor",
                "gel_formulation": config.material.formulation,
            },
        )
        record = {
            "status": "running",
            "variant": variant,
            "states": [],
            "diagnostic_only": True,
        }
        results[variant] = record
        write_json(result_file, results)
        started = time.perf_counter()
        base = (
            AnsysPlane
            if config.is_plane
            else AnsysImported
            if config.imported_mesh
            else AnsysGel
        )

        class Diagnostic(base):
            def build(self):
                super().build()
                commands = ["FINISH", "/PREP7"]
                if variant in ("projection", "mixed_projection"):
                    commands.append("KEYOPT,2,4,3")
                if variant == "unified":
                    commands.append("KEYOPT,2,4,5")
                if variant == "softer":
                    commands.append("RMODIF,1,3,0.1")
                commands += ["FINISH", "/SOLU"]
                if variant == "no_predictor":
                    commands.append("PRED,OFF")
                commands.append("FINISH")
                self.command_block(commands, "diagnostic_controls")

        try:
            with Diagnostic(config, directory / "solver", args.exec_file) as model:
                record["gel_elements"] = len(model.mesh.hexes)
                record["object_elements"] = (
                    len(model.object_mesh.hexes) if model.object_mesh else 0
                )
                if config.is_plane:
                    targets = [args.stop_time]
                    iterator = (
                        (state, pose)
                        for at in targets
                        for state, pose, _ in model.solve_interval(
                            config.physical_pose(at)
                        )
                    )
                else:

                    def states():
                        for index, pose in enumerate(config.trajectory):
                            if pose.time_s > args.stop_time + 1e-12:
                                break
                            state = (
                                model.reference_state()
                                if index == 0
                                else model.solve(pose, index)[0]
                            )
                            yield state, pose

                    iterator = states()
                for state, pose in iterator:
                    force = state.contact_force_n.sum(axis=0)
                    error = float(np.linalg.norm(force + state.backing_reaction_n))
                    if error > max(
                        config.solver.balance_tolerance * np.linalg.norm(force), 1e-6
                    ):
                        raise RuntimeError("Force balance failed")
                    record["states"].append(
                        {
                            "time_s": float(pose.time_s),
                            "force_n": force.tolist(),
                            "balance_error_n": error,
                            "max_displacement_m": float(
                                np.linalg.norm(state.displacement_m, axis=1).max()
                            ),
                        }
                    )
                    state.save(directory / "last_state.npz")
                    write_json(result_file, results)
                if not record["states"]:
                    raise RuntimeError("No converged states returned")
                record["solve_command_s"] = getattr(model, "last_timings", {}).get(
                    "solve_command_s"
                )
                record["status"] = "passed"
        except Exception as error:
            record.update(
                status="failed", error_type=type(error).__name__, error=str(error)
            )
            (directory / "error.log").write_text(traceback.format_exc())
        record["elapsed_s"] = time.perf_counter() - started
        write_json(result_file, results)
        print(json.dumps({k: v for k, v in record.items() if k != "states"}), flush=True)
    return 0
