"""Find the platen travel that puts each specimen at a target normal force.

Equal commanded travel does not mean equal load: a rigid specimen leaves the gel
carrying everything, while foam and fabric absorb most of it. Matching materials
at a force therefore needs one measured force-travel curve per material, not one
travel copied across them.

`prepare` writes a deep monotonic press per material and a variants file the
sweep supervisor runs. `apply` reads those runs back, interpolates the travel at
the target force, and writes the shipped force-matched configs. A ramp that
aborts at high load is still usable: the target is crossed early.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CASES = ROOT / "configs/material_plane_slide"
# Generous press depth per material, from its specimen compliance. The ramp only
# has to cross the target; overshoot costs solve time, undershoot wastes the run.
RAMP_DEPTH_M = {
    "rigid_reference": 0.0006,
    "slippery_surface": 0.0006,
    "rough_surface": 0.0006,
    "sticky_surface": 0.0006,
    "soft_rubber": 0.0012,
    "compressible_foam": 0.0025,
    "fluffy_fabric": 0.0025,
}
NATIVE = {"fluffy_fabric", "sticky_surface"}
PRELOAD_M = 0.00005  # Light enough not to overshoot the target on its own.


def keyframe(time_s, travel_m, x_m=0.0):
    return {
        "time_s": time_s,
        "normal_travel_m": travel_m,
        "x_m": x_m,
        "y_m": 0,
        "twist_rad": 0,
    }


def calibration_suite(base, depth):
    """A press-only protocol: no slide, no release, nothing but a travel ramp."""
    suite = json.loads(json.dumps(base))
    suite["name"] = "material_plane_force_calibration"
    suite["sensor"]["material"]["formulation"] = "mixed_up"
    suite["contact_numerics"]["stabilization_damping"] = {
        "normal_factor": 1e-3,
        "activation": "always",
    }
    protocol = suite["protocol"]
    protocol["initialization"]["end_normal_travel_m"] = PRELOAD_M
    protocol["recorded_interval_s"] = [0, 2.0]
    protocol["release"] = False
    protocol.pop("release_start_time_s", None)
    protocol["allow_recorded_lift_off"] = False
    protocol["phases"] = [{"name": "press", "start_time_s": 0, "end_time_s": 2}]
    protocol["keyframes"] = [
        keyframe(t, PRELOAD_M + (depth - PRELOAD_M) * t / 2.0)
        for t in (0.0, 0.5, 1.0, 1.5, 2.0)
    ]
    protocol["notes"] = ["Force-travel calibration ramp; not a dataset protocol."]
    # The ramp deliberately leaves full contact as the edge opens under load.
    acceptance = suite["contact_acceptance"]
    acceptance["macroscopic_contact_bins"]["required_active_bin_fraction"] = 0
    acceptance["minimum_geometric_footprint_coverage_fraction"] = 0
    acceptance["minimum_plane_edge_margin_m"] = 0
    return suite


def shipped_suite(base, travel, target_n, provenance):
    """The measured travel, dropped into the full press/slide/release protocol."""
    suite = json.loads(json.dumps(base))
    suite["name"] = "material_plane_press_slide_release_force_matched"
    suite["sensor"]["material"]["formulation"] = "mixed_up"
    suite["contact_numerics"]["stabilization_damping"] = {
        "normal_factor": 1e-3,
        "activation": "always",
    }
    protocol = suite["protocol"]
    preload = round(travel / 3, 9)
    protocol["initialization"]["end_normal_travel_m"] = preload
    protocol["normal_force_target_n"] = target_n
    protocol["travel_calibration"] = provenance
    protocol["recorded_interval_s"] = [0, 8.0]
    protocol["release"] = True
    protocol["release_start_time_s"] = 6.0
    protocol["allow_recorded_lift_off"] = True
    protocol["phases"] = [
        {"name": "press", "start_time_s": 0, "end_time_s": 2},
        {"name": "hold_after_press", "start_time_s": 2, "end_time_s": 3},
        {
            "name": "slide_at_fixed_compression",
            "start_time_s": 3,
            "end_time_s": 5,
            "speed_m_s": 0.005,
        },
        {"name": "hold_after_slide", "start_time_s": 5, "end_time_s": 6},
        {"name": "release", "start_time_s": 6.0, "end_time_s": 8.0},
    ]
    ramp = [(0.0, preload), (0.5, 0.0), (1.0, 0.0), (1.5, 0.0), (2.0, travel)]
    keyframes = [
        keyframe(t, round(preload + (travel - preload) * t / 2.0, 9)) for t, _ in ramp
    ]
    keyframes += [
        keyframe(3.0, travel),
        keyframe(3.5, travel, 0.0025),
        keyframe(4.0, travel, 0.005),
        keyframe(4.5, travel, 0.0075),
        keyframe(5.0, travel, 0.010),
        keyframe(6.0, travel, 0.010),
        keyframe(7.6, 0.0, 0.010),
        keyframe(8.0, -round(travel / 3, 9), 0.010),
    ]
    protocol["keyframes"] = keyframes
    protocol["notes"] = [
        f"Travel is set so the press ends at {target_n} N, measured for this "
        "specimen. It is not transferable to another material.",
        "The preload is one third of the press travel so most of the load change "
        "happens inside the recorded press phase.",
        "Compression is fixed during sliding; the release lifts clear of first touch.",
    ]
    return suite


def prepare(output, target_n, only):
    output.mkdir(parents=True, exist_ok=True)
    folder = output / "calibration_configs"
    folder.mkdir(exist_ok=True)
    base = json.loads((CASES / "suite.json").read_text(encoding="utf-8"))
    variants = []
    for name, depth in RAMP_DEPTH_M.items():
        if only and name not in only:
            continue
        case = json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))
        case["name"] = f"calibrate_{name}"
        case["setup"] = f"suite_calibration_{name}.json"
        case["status"] = "diagnostic_variant"
        case["object"]["geometry"].update(width_m=0.052, length_m=0.028)
        # The case is written outside configs/, so its material reference has to
        # stop being relative to the directory it came from.
        material = case["object"]["material"]
        if "case" in material:
            material["case"] = str((CASES / material["case"]).resolve())
        (folder / f"suite_calibration_{name}.json").write_text(
            json.dumps(calibration_suite(base, depth), indent=2) + "\n", encoding="utf-8"
        )
        (folder / f"calibrate_{name}.json").write_text(
            json.dumps(case, indent=2) + "\n", encoding="utf-8"
        )
        arguments = ["--diagnose"]
        if name in NATIVE:
            arguments += ["--libraries", str(ROOT / "outputs/plane_adapters_v9")]
        variants.append(
            {
                "name": name,
                "config": str((folder / f"calibrate_{name}.json").resolve()),
                "arguments": arguments,
                "common": [
                    "--maximum-time-increment-s",
                    "0.02",
                    "--solve-interval-s",
                    "0.02",
                    "--sample-interval-s",
                    "0.2",
                    "--render-scale",
                    "1",
                ],
                "question": f"What travel puts {name} at {target_n} N?",
            }
        )
    settings = {"common": [], "variants": variants, "target_n": target_n}
    (output / "calibration-variants.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(variants)} calibration ramps in {folder}")
    for v in variants:
        print(f"  {v['name']:<20} ramp to {RAMP_DEPTH_M[v['name']] * 1e3:.2f} mm")
    return 0


def curve(run):
    """Recorded travel and force, including the preload ramp before frame 0."""
    from gelsight_ansys.config import Config

    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    config = Config.load(run / "config.json")
    points = []
    for record in summary.get("initialization_substeps", []) + summary.get(
        "recorded_substeps", []
    ):
        travel = config.physical_pose(record["time_s"]).depth_m
        points.append((travel, record["normal_force_n"]))
    points.sort()
    travel = np.array([p[0] for p in points])
    force = np.array([p[1] for p in points])
    keep = np.r_[True, np.diff(travel) > 0]
    return travel[keep], force[keep]


def apply(sweep, target_n, only):
    settings = json.loads(
        (sweep / "calibration-variants.json").read_text(encoding="utf-8")
    )
    base = json.loads((CASES / "suite.json").read_text(encoding="utf-8"))
    results = {}
    for variant in settings["variants"]:
        name = variant["name"]
        if only and name not in only:
            continue
        runs = sorted((sweep / name / "runs").glob("*/summary.json"))
        if not runs:
            print(f"  {name:<20} no run output yet", file=sys.stderr)
            continue
        travel, force = curve(runs[-1].parent)
        if force.max() < target_n:
            print(
                f"  {name:<20} ramp stopped at {force.max():.2f} N below the "
                f"{target_n} N target; deepen RAMP_DEPTH_M and re-run",
                file=sys.stderr,
            )
            continue
        matched = float(np.interp(target_n, force, travel))
        results[name] = {
            "travel_m": matched,
            "target_n": target_n,
            "measured_span_n": [float(force.min()), float(force.max())],
            "run": runs[-1].parent.name,
        }
        provenance = (
            f"{matched * 1e3:.4f} mm gives {target_n} N for this specimen, "
            f"interpolated from the calibration ramp {runs[-1].parent.name}."
        )
        case = json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))
        case["name"] = f"plane_{name}_press_slide_release_5n"
        case["setup"] = f"suite_5n_{name}.json"
        case["status"] = "diagnostic_variant"
        case["object"]["geometry"].update(width_m=0.052, length_m=0.028)
        case["model_notes"] = list(case.get("model_notes", [])) + [provenance]
        (CASES / f"suite_5n_{name}.json").write_text(
            json.dumps(shipped_suite(base, matched, target_n, provenance), indent=2)
            + "\n",
            encoding="utf-8",
        )
        (CASES / f"{name}_5n.json").write_text(
            json.dumps(case, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  {name:<20} {matched * 1e3:8.4f} mm -> {target_n} N")
    (sweep / "calibration-results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    parser.add_argument("--sweep", type=Path, required=True)
    parser.add_argument("--target-n", type=float, default=5.0)
    parser.add_argument("--only", action="append")
    args = parser.parse_args(argv)
    only = set(args.only) if args.only else None
    if args.mode == "prepare":
        return prepare(args.sweep.resolve(), args.target_n, only)
    return apply(args.sweep.resolve(), args.target_n, only)


if __name__ == "__main__":
    raise SystemExit(main())
