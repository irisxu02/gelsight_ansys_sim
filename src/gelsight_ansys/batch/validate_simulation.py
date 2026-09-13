"""Opt-in licensed integration runs; all artifacts stay under --output."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .presets import INDENTER_CASES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=(*INDENTER_CASES, "examples", "formulations", "all"),
        default="all",
    )
    parser.add_argument("--output", type=Path, default=Path("outputs/validation"))
    parser.add_argument(
        "--render-scale",
        type=int,
        default=1,
        help="Optical resolution multiplier; preserves FOV and physical marker layout",
    )
    parser.add_argument("--allow-unlisted-gpu", action="store_true")
    parser.add_argument("--exec-file", type=Path)
    args = parser.parse_args(argv)
    from gelsight_ansys.config import Config, Pose
    from gelsight_ansys.pipeline import run

    root = Path(__file__).resolve().parents[3]
    base = Config.load(root / "configs/sphere_press.json").with_render_scale(
        args.render_scale
    )
    base = replace(
        base, solver=replace(base.solver, allow_unlisted_gpu=args.allow_unlisted_gpu)
    )
    cases = {}
    for key, name in INDENTER_CASES.items():
        if args.case in (key, "examples", "all"):
            config = Config.load(root / "configs" / f"{name}.json").with_render_scale(
                args.render_scale
            )
            cases[key] = replace(
                config,
                solver=replace(config.solver, allow_unlisted_gpu=args.allow_unlisted_gpu),
            )
    coarse = replace(
        base, gel=replace(base.gel, elements=(16, 12, 4), contact_element_size_m=None)
    )
    if args.case in ("formulations", "all"):
        trajectory = (Pose(0, -0.0001), Pose(0.2, 0.0004), Pose(0.4, -0.0001))
        reference = replace(coarse, name="displacement_reference", trajectory=trajectory)
        cases["displacement_reference"] = reference
        cases["mixed_up_reference"] = replace(
            reference,
            name="mixed_up_reference",
            material=replace(reference.material, formulation="mixed_up"),
            solver=replace(reference.solver, require_gpu=False),
        )
        cases["refined_reference"] = replace(
            reference, name="refined_reference", gel=base.gel
        )
    result_file = args.output / "validation.json"
    results = json.loads(result_file.read_text()) if result_file.exists() else {}
    for name, config in cases.items():
        print(f"Validating {name}", flush=True)
        directory, summary = run(config, args.output, args.exec_file)
        frames = summary["frames"]
        assert [frame["load_step"] for frame in frames] == list(range(len(frames))), (
            "Lost load-step history"
        )
        assert abs(frames[-1]["normal_force_n"]) < 1e-6, "Contact remains after release"
        assert frames[-1]["max_surface_displacement_m"] < 1e-8, (
            "Gel did not recover after release"
        )
        assert max(f["normal_force_n"] for f in frames) > 0.001, (
            "Insufficient contact force"
        )
        if name in ("slide", "rough", "imported_pyramid"):
            assert max(abs(f["force_on_gel_n"][0]) for f in frames) > 0.005, (
                "Missing shear response"
            )
        if config.indenter.deformable:
            assert frames[-1]["max_indenter_deformation_m"] < 1e-8, (
                "Object did not recover after release"
            )
            assert max(f["max_indenter_deformation_m"] for f in frames) > 1e-5, (
                "Object compliance missing"
            )
            assert max(f["indenter_grip_displacement_error_m"] for f in frames) < 1e-10, (
                "Grip motion mismatch"
            )
        if name == "twist":
            assert max(abs(f["moment_on_gel_nm"][2]) for f in frames) > 1e-6, (
                "Missing torsional response"
            )
            import numpy as np

            index = max(
                range(len(frames)), key=lambda i: abs(frames[i]["moment_on_gel_nm"][2])
            )
            assert frames[index]["moment_on_gel_nm"][2] > 0, (
                "Sphere torque has the wrong sign"
            )
            with np.load(
                directory / "fields" / f"frame_{index:04d}.npz", allow_pickle=False
            ) as fields:
                ref = fields["marker_reference_m"][:, :2]
                delta = fields["marker_position_m"][:, :2] - ref
                circulation = np.sum(ref[:, 0] * delta[:, 1] - ref[:, 1] * delta[:, 0])
                assert circulation > 1e-10, (
                    "Sphere spin did not produce counterclockwise material-marker motion"
                )
        from PIL import Image

        with Image.open(directory / "process.gif") as animation:
            assert animation.n_frames == len(frames), (
                "Process GIF is missing trajectory frames"
            )
        assert not (directory / "preview.gif").exists()
        assert (
            directory / "tactile.gif"
        ).is_file() == config.visualization.save_tactile_gif
        assert (directory / "panels").is_dir() == config.visualization.save_panel_frames
        assert all(
            (directory / "images" / f"frame_{i:04d}.png").is_file()
            for i in range(len(frames))
        )
        results[name] = {
            "status": "passed",
            "frame_count": len(frames),
            "mesh_elements": list(config.gel.elements),
            "material": config.to_dict()["material"],
            "coating_model": summary["coating_model"],
            "render_mode": config.optics.render_mode,
            "force_norm": config.solver.force_norm,
            "indenter": config.to_dict()["indenter"],
            "max_penetration_m": max(f["max_penetration_m"] for f in frames),
            "max_sticking_elastic_slip_m": max(
                (f["max_sticking_elastic_slip_m"] or 0) for f in frames
            ),
            "max_indenter_deformation_m": max(
                f.get("max_indenter_deformation_m", 0) for f in frames
            ),
            "gel_dimensions_m": [
                config.gel.width_m,
                config.gel.length_m,
                config.gel.thickness_m,
            ],
            "camera_projection": config.camera.projection,
            "image_width_height_px": [config.camera.width_px, config.camera.height_px],
            "marker_grid_rows_cols": config.optics.marker_grid_rows_cols,
            "max_depth_m": max(p.depth_m for p in config.trajectory),
            "max_twist_rad": max(abs(p.twist_rad) for p in config.trajectory),
            "max_shear_force_n": max(
                sum(v * v for v in f["force_on_gel_n"][:2]) ** 0.5 for f in frames
            ),
            "max_torque_z_nm": max(abs(f["moment_on_gel_nm"][2]) for f in frames),
            "max_marker_in_plane_displacement_m": max(
                f["max_marker_in_plane_displacement_m"] for f in frames
            ),
            "peak_force_n": max(f["normal_force_n"] for f in frames),
            "max_balance_error_n": max(f["force_balance_error_n"] for f in frames),
            "max_raster_error_n": max(f["raster_force_error_n"] for f in frames),
            "gpu_mechanics_verified": summary["gpu_mechanics_verified"],
            "render_device": summary["render_device"],
            "elapsed_s": summary["elapsed_s"],
            "run": directory.name,
        }
        (args.output / "validation.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
