"""Licensed mesh-import checks on small meshes; no example exports are replaced."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gelsight_ansys.artifacts import write_json
from gelsight_ansys.config import Config
from gelsight_ansys.pipeline import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exec-file", type=Path)
    parser.add_argument("--cores", type=int, default=1)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = Config.load(ROOT / "configs/imported_rigid_press.json")
    base = replace(base, gel=replace(base.gel, elements=(16, 12, 4))).with_solver(
        cores=args.cores
    )
    reference = replace(
        base,
        name="generated_flat_reference",
        imported_mesh=None,
        indenter=replace(
            base.indenter, shape="flat", half_width_m=0.0015, half_length_m=0.0015
        ),
    )
    fixture = args.output / "quad.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "config_kind": "object_mesh",
                "vertices": [
                    [-1.5, -1.5, 0],
                    [-1.5, 1.5, 0],
                    [1.5, 1.5, 0],
                    [1.5, -1.5, 0],
                ],
                "faces": [[0, 1, 2, 3]],
            }
        )
    )
    from gelsight_ansys.mesh_import import import_mesh

    geometry = {
        "shape": "mesh",
        "file": str(fixture.resolve()),
        "units": "mm",
        "clearance_m": 0.0001,
        "reference_point_m": [0, 0, 0.0001],
        "transform": {"translation_m": [0, 0, 0.0001], "rotation_xyzw": [0, 0, 0, 1]},
    }
    quad = replace(
        base,
        name="imported_quad_reference",
        imported_mesh=import_mesh(geometry, ROOT / "configs", False),
    ).validate()
    soft = Config.load(ROOT / "configs/imported_soft_press.json")
    soft = replace(soft, gel=base.gel).with_solver(cores=args.cores)
    results = {}
    summaries = {}
    for config in (reference, quad, base, soft):
        directory, summary = run(config, args.output, args.exec_file)
        frames = summary["frames"]
        if summary["status"] != "passed" or summary["render_device"] != "cuda:0":
            raise RuntimeError("A complete ANSYS solve and CUDA rendering are required")
        if (
            abs(frames[-1]["normal_force_n"]) >= 1e-6
            or frames[-1]["max_surface_displacement_m"] >= 1e-8
        ):
            raise RuntimeError("Mesh-import release did not recover the unloaded state")
        if config.indenter.deformable:
            if max(f["max_indenter_deformation_m"] for f in frames) <= 1e-5:
                raise RuntimeError("Imported volume did not deform")
            if max(f["indenter_grip_displacement_error_m"] for f in frames) >= 1e-10:
                raise RuntimeError("Imported grip does not follow prescribed motion")
        summaries[config.name] = summary
        results[config.name] = {
            "status": "passed",
            "run": directory.name,
            "frames": len(frames),
            "peak_force_n": max(f["normal_force_n"] for f in frames),
            "max_balance_error_n": max(f["force_balance_error_n"] for f in frames),
            "render_device": summary["render_device"],
            "gel_elements": list(config.gel.elements),
        }
        write_json(args.output / "validation.json", results)
    original = np.array(
        [f["force_on_gel_n"] for f in summaries[reference.name]["frames"]]
    )
    imported = np.array([f["force_on_gel_n"] for f in summaries[quad.name]["frames"]])
    np.testing.assert_allclose(imported, original, rtol=2e-3, atol=1e-6)
    results["equivalent_flat_targets"] = {
        "status": "passed",
        "maximum_force_difference_n": float(np.max(np.abs(imported - original))),
    }
    write_json(args.output / "validation.json", results)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
