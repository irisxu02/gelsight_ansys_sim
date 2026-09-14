"""Compare timestep and object-mesh candidates on identical early rubber preload."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..artifacts import write_json
from ..config import Config
from ..contracts import SurfaceState
from ..surface import Markers
from .contact_benchmark import main as contact_benchmark

ROOT = Path(__file__).resolve().parents[3]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-time", type=float, default=-1.9)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    config = Config.load(ROOT / "configs/material_plane_slide/soft_rubber.json")
    results = {}
    reference = None
    for name, dt, size in (
        ("reference", 0.01, 0.0005),
        ("time_step", 0.02, 0.0005),
        ("object_mesh", 0.02, 0.00075),
        ("moderate_object_mesh", 0.02, 0.000625),
    ):
        root = args.output / name
        contact_benchmark(
            [
                "--config",
                str(ROOT / "configs/material_plane_slide/soft_rubber.json"),
                "--output",
                str(root),
                "--variants",
                "baseline",
                "--stop-time",
                str(args.stop_time),
                "--dt",
                str(dt),
                "--object-size",
                str(size),
            ]
        )
        result = json.loads((root / "benchmark.json").read_text())["baseline"]
        record = {k: v for k, v in result.items() if k != "states"}
        record.update(dt_s=dt, object_size_m=size, scope="early_preload_only")
        results[name] = record
        if result["status"] != "passed":
            write_json(args.output / "comparison.json", results)
            if result["status"] == "blocked":
                return 2
            continue
        state = SurfaceState.load(root / "baseline/last_state.npz")
        if reference is None:
            if name != "reference":
                record["comparison_status"] = "missing_reference"
                write_json(args.output / "comparison.json", results)
                continue
            reference = state, record
        baseline, timing = reference
        np.testing.assert_array_equal(state.reference_m, baseline.reference_m)
        force_error = np.linalg.norm(
            state.contact_force_n.sum(axis=0) - baseline.contact_force_n.sum(axis=0)
        )
        force_scale = max(np.linalg.norm(baseline.contact_force_n.sum(axis=0)), 1e-6)
        displacement_error = np.linalg.norm(
            state.displacement_m - baseline.displacement_m, axis=1
        ).max()
        displacement_scale = max(
            np.linalg.norm(baseline.displacement_m, axis=1).max(), 1e-8
        )
        rest = replace(baseline, displacement_m=np.zeros_like(baseline.displacement_m))
        markers = Markers(
            rest, config.optics.marker_spacing_m, config.camera, config.optics
        )
        marker_error = np.linalg.norm(
            markers.positions(state) - markers.positions(baseline), axis=1
        ).max()
        record.update(
            force_relative_error=float(force_error / force_scale),
            displacement_relative_error=float(displacement_error / displacement_scale),
            marker_max_error_m=float(marker_error),
            solve_speedup=timing["solve_command_s"] / record["solve_command_s"],
            total_speedup=timing["elapsed_s"] / record["elapsed_s"],
            comparison_status="passed"
            if force_error <= 0.02 * force_scale
            and displacement_error <= max(0.02 * displacement_scale, 1e-7)
            and marker_error <= 1e-6
            else "outside_tolerance",
        )
        write_json(args.output / "comparison.json", results)
        print(json.dumps(record), flush=True)
    return 0
