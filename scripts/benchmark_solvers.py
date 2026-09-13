"""Sequential 0.4 mm sphere loading/release benchmark with unchanged force guards."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.config import Config, Pose
from gelsight_ansys.contracts import SurfaceState
from gelsight_ansys.pipeline import run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/sphere_press.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/solver-benchmarks")
    )
    parser.add_argument("--exec-file", type=Path)
    parser.add_argument("--allow-unlisted-gpu", action="store_true")
    parser.add_argument(
        "--include-cpu-reference",
        action="store_true",
        help="Also test four CPU solver cores; keep the configured optical backend",
    )
    args = parser.parse_args(argv)
    base = Config.load(args.config)
    if base.indenter.shape != "sphere":
        raise ValueError("This benchmark requires a sphere preset")
    base = replace(
        base,
        trajectory=tuple(
            Pose(i * 0.2, d / 1000)
            for i, d in enumerate(
                [
                    -base.indenter.clearance_m * 1000,
                    0,
                    0.2,
                    0.4,
                    0.2,
                    0,
                    -base.indenter.clearance_m * 1000,
                ]
            )
        ),
        solver=replace(base.solver, allow_unlisted_gpu=args.allow_unlisted_gpu),
    )
    cases = {
        name: replace(
            base,
            name="benchmark_" + name,
            solver=replace(
                base.solver, equation_solver=name, cores=3, gpu=True, require_gpu=True
            ),
        )
        for name in ("sparse", "mixed")
    }
    if args.include_cpu_reference:
        cases["sparse_cpu4"] = replace(
            base,
            name="benchmark_sparse_cpu4",
            solver=replace(
                base.solver,
                equation_solver="sparse",
                cores=4,
                gpu=False,
                require_gpu=False,
            ),
        )
    results = {}
    reference = None
    for name, config in cases.items():
        print(f"Benchmarking {name}", flush=True)
        directory, summary = run(
            config, args.output, args.exec_file, args.config.parent
        )
        frames = summary["frames"]
        if (
            abs(frames[-1]["normal_force_n"]) >= 1e-6
            or frames[-1]["max_surface_displacement_m"] >= 1e-8
        ):
            raise RuntimeError("The benchmark did not recover after release")
        forces = np.asarray([f["force_on_gel_n"] for f in frames])
        states = [
            SurfaceState.load(directory / "states" / f"frame_{i:04d}.npz")
            for i in range(len(frames))
        ]
        displacements = np.asarray([s.displacement_m for s in states])
        if reference is None:
            reference = forces, displacements
        np.testing.assert_allclose(forces, reference[0], rtol=0.02, atol=1e-6)
        np.testing.assert_allclose(displacements, reference[1], rtol=5e-4, atol=1e-8)
        results[name] = {
            "status": "passed",
            "run": directory.name,
            "elapsed_s": summary["elapsed_s"],
            "solve_command_s": sum(
                f.get("timings", {}).get("solve_command_s", 0) for f in frames
            ),
            "peak_force_n": max(f["normal_force_n"] for f in frames),
            "max_force_difference_n": float(np.abs(forces - reference[0]).max()),
            "max_surface_displacement_difference_m": float(
                np.abs(displacements - reference[1]).max()
            ),
            "solver_gpu_verified": summary["gpu_mechanics_verified"],
            "render_device": summary["render_device"],
            "projection_device": summary["projection_device"],
        }
        (args.output / "benchmark.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(results[name]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
