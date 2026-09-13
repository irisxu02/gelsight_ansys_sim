"""Verify exported snapshots against current presets, manifests, and RGB fields."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from gelsight_ansys.config import Config
from gelsight_ansys.metrics import validate_frame


def audit(folder, preset, render_scale=1):
    config = Config.load(folder / "config.json", validate=False)
    plane = config.is_plane
    expected = Config.load(preset).with_render_scale(render_scale)
    normalized = (
        config
        if plane
        else config.with_solver(allow_unlisted_gpu=expected.solver.allow_unlisted_gpu)
    )
    # JSON snapshots use lists; scaled dataclass settings may still use tuples.
    if json.loads(json.dumps(normalized.to_dict())) != json.loads(
        json.dumps(expected.to_dict())
    ):
        raise ValueError(f"Export does not match current preset: {folder.name}")
    summary = json.loads((folder / "summary.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    count = len(config.trajectory)
    assert summary["status"] == "passed" and len(summary["frames"]) == count
    assert manifest["frame_count"] == count
    assert config.optics.render_mode == "raw"
    if config.solver.require_gpu:
        assert summary["gpu_mechanics_verified"]
    if not config.solver.gpu:
        assert not summary["gpu_mechanics_verified"]
    assert summary["render_device"] == "cuda:0"
    assert summary["projection_device"] == "cuda:0"
    for relative, record in manifest["files"].items():
        p = (folder / relative).resolve()
        p.relative_to(folder.resolve())
        with p.open("rb") as stream:
            assert (
                hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]
            ), relative
        assert p.stat().st_size == record["bytes"], relative
    with Image.open(
        folder / ("unloaded_reference.png" if plane else "images/frame_0000.png")
    ) as image:
        reference = np.asarray(image).astype(np.int16)
    with np.load(folder / "solid_mesh.npz", allow_pickle=False) as mesh:
        if plane:
            from gelsight_ansys.plane_mesh import gel_mesh

            expected_mesh = gel_mesh(
                config.specification, config.plane_options.element_size_m
            )
            np.testing.assert_array_equal(mesh["gel_hexes"], expected_mesh.hexes)
            np.testing.assert_allclose(
                mesh["gel_reference_m"], expected_mesh.coordinates, atol=1e-14, rtol=0
            )
            np.testing.assert_array_equal(
                mesh["surface_nodes"], expected_mesh.surface_nodes
            )
        else:
            assert len(mesh["gel_hexes"]) == np.prod(config.gel.elements)
        assert np.all(mesh["gel_material_ids"] == 1)
    for i, metric in enumerate(summary["frames"]):
        validate_frame(metric, config)
        if plane:
            checkpoints = config.specification.solve_times
            step = np.flatnonzero(
                np.isclose(checkpoints, config.trajectory[i].time_s, rtol=0, atol=1e-10)
            )
            assert len(step) == 1 and metric["load_step"] == int(step[0]) + 1
        else:
            assert metric["load_step"] == i
        if plane:
            assert abs(metric["time_s"] - config.trajectory[i].time_s) < 1e-10
        with Image.open(folder / "images" / f"frame_{i:04d}.png") as image:
            raw = np.asarray(image).astype(np.int16)
        with np.load(folder / "fields" / f"frame_{i:04d}.npz", allow_pickle=False) as f:
            np.testing.assert_array_equal(f["rgb_difference_int16"], raw - reference)
            np.testing.assert_allclose(
                f["marker_flow_pixel"],
                f["marker_pixel"] - f["marker_reference_pixel"],
                atol=1e-13,
            )
            assert len(f["marker_pixel"]) == np.prod(config.optics.marker_grid_rows_cols)
    if plane:
        from gelsight_ansys.plane_coverage import ContactCoverage

        checker = object.__new__(ContactCoverage)
        checker.rules = config.specification.suite["contact_acceptance"]
        assert summary["production_mesh"] and summary["complete_recorded_interval"]
        assert summary["initialization_substeps"] and summary["recorded_substeps"]
        for sample in summary["recorded_substeps"]:
            checker.validate(
                sample["contact_coverage"],
                require_contact=config.specification.requires_contact(sample["time_s"]),
            )
        if config.specification.suite["protocol"].get("release", False):
            assert summary.get("release_verified")
        times = [r["time_s"] for r in summary["recorded_substeps"]]
        assert (
            max(np.diff(times))
            <= config.specification.suite["solver"]["maximum_time_increment_s"] + 1e-9
        )
    else:
        assert abs(summary["frames"][-1]["normal_force_n"]) < 1e-6
        assert summary["frames"][-1]["max_surface_displacement_m"] < 1e-8
    visualization = json.loads((folder / "visualization.json").read_text())
    viewer_folder = visualization.get("frame_viewer_folder", "panels")
    assert viewer_folder in ("images", "panels")
    for i in range(count):
        assert f"{viewer_folder}/frame_{i:04d}.png" in manifest["files"]
    if visualization.get("tactile_gif_saved", True):
        assert "tactile.gif" in manifest["files"]
    with Image.open(folder / "process.gif") as gif:
        assert gif.n_frames == count
    return {
        "status": "passed",
        "frames": count,
        "verified_files": len(manifest["files"]),
        "raw_difference_identity": True,
        "current_preset_match": True,
        "gpu_mechanics_verified": summary["gpu_mechanics_verified"],
        "render_device": summary["render_device"],
        "projection_device": summary["projection_device"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path("docs/examples"))
    parser.add_argument("--configs", type=Path, default=Path("configs"))
    parser.add_argument("--report", type=Path, default=Path("docs/example-audit.json"))
    parser.add_argument("--render-scale", type=int, default=1)
    args = parser.parse_args()
    results = {
        p.stem: audit(args.examples / p.stem, p, args.render_scale)
        for p in sorted(args.configs.glob("*.json"))
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
