"""Compare CPU/CUDA projection and rendering of saved states without ANSYS."""

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.camera import optical_surface
from gelsight_ansys.config import Config
from gelsight_ansys.contracts import SurfaceState
from gelsight_ansys.optics import Renderer
from gelsight_ansys.surface import Markers, image_coordinates, project_surface


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/projection-benchmark.json")
    )
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    config = Config.load(args.run / "config.json")
    summary = json.loads((args.run / "summary.json").read_text())
    paths = sorted((args.run / "states").glob("frame_*.npz"))
    if summary["status"] != "passed" or len(paths) != len(config.trajectory):
        raise ValueError("A complete passed run is required")
    states = [SurfaceState.load(p) for p in paths]
    markers = Markers(
        states[0], config.optics.marker_spacing_m, config.camera, config.optics
    )
    reference = image_coordinates(markers.reference_m, config.camera)
    pixels = [image_coordinates(markers.positions(s), config.camera) for s in states]
    samples = {
        backend: {stage: [] for stage in ("surface_s", "camera_s", "render_s")}
        for backend in ("cpu", "cuda")
    }
    outputs = {}
    for backend in samples:
        renderer = Renderer(
            replace(config, optics=replace(config.optics, backend=backend)), args.run
        )
        for repeat in range(args.repeats + 1):
            for i, state in enumerate(states):
                t = time.perf_counter()
                fields = project_surface(state, config.camera, backend=backend)
                a = time.perf_counter()
                fields.update(
                    optical_surface(state, config.camera, fields, backend=backend)
                )
                b = time.perf_counter()
                rgb = renderer.render(fields, pixels[i], reference)
                c = time.perf_counter()
                if repeat:
                    for stage, elapsed in zip(samples[backend], (a - t, b - a, c - b)):
                        samples[backend][stage].append(elapsed)
                if repeat == 0:
                    outputs[backend, i] = (fields, rgb)
        print(f"Completed {backend} benchmark", flush=True)
    errors = {}
    max_rgb_error = 0
    for i in range(len(states)):
        cpu, cpu_rgb = outputs["cpu", i]
        cuda, cuda_rgb = outputs["cuda", i]
        for key in cpu:
            if cpu[key].dtype.kind in "biu":
                np.testing.assert_array_equal(cpu[key], cuda[key], err_msg=key)
            else:
                np.testing.assert_allclose(
                    cpu[key],
                    cuda[key],
                    atol=1e-7 if key == "force_density_pa" else 3e-12,
                    rtol=2e-9,
                    err_msg=key,
                )
            error = float(
                np.max(np.abs(cpu[key].astype(float) - cuda[key].astype(float)))
            )
            errors[key] = max(errors.get(key, 0), error)
        max_rgb_error = max(
            max_rgb_error, int(np.abs(cpu_rgb.astype(int) - cuda_rgb.astype(int)).max())
        )
    if max_rgb_error > 1:
        raise RuntimeError("CPU/CUDA RGB disagreement exceeds one intensity level")
    timings = {
        backend: {
            stage: {
                "mean_s": statistics.mean(values),
                "median_s": statistics.median(values),
                "min_s": min(values),
                "max_s": max(values),
            }
            for stage, values in stages.items()
        }
        for backend, stages in samples.items()
    }
    result = {
        "status": "passed",
        "scope": "Saved-state projection and rendering only; excludes mechanics, file I/O and report generation",
        "frame_count": len(states),
        "repeats": args.repeats,
        "warmup": "one complete sequence per backend",
        "image_width_height_px": [config.camera.width_px, config.camera.height_px],
        "timings": timings,
        "field_max_absolute_errors": errors,
        "max_rgb_error_dn": max_rgb_error,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
