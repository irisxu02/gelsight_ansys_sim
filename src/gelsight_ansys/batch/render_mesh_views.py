"""Draw finite-element views of a finished run, without solving it again.

A run that was recorded without `visualization.save_mesh_frames`, or one whose
views should be redrawn at a different exaggeration or viewpoint, already holds
everything the pictures are made of: the mesh in solid_mesh.npz, the nodal
solution in bodies/, and the contact state in states/. This reads those back and
writes mesh/frame_NNNN.png and mesh.gif exactly as a run would have.
"""

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from gelsight_ansys.artifacts import ImageFiles, save_gif
from gelsight_ansys.config import Config
from gelsight_ansys.contracts import SurfaceState
from gelsight_ansys.fem_view import MeshView


def geometry_of(directory):
    """The bodies a run solved, read back from the record it wrote of them."""
    with np.load(directory / "solid_mesh.npz", allow_pickle=False) as arrays:
        geometry = {
            key: arrays[key]
            for key in ("gel_reference_m", "gel_hexes", "surface_nodes")
        }
        if "target_reference_m" in arrays.files:
            geometry["target"] = arrays["target_reference_m"]
    return geometry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="A finished run directory")
    parser.add_argument(
        "--deformation-scale",
        type=float,
        help="Exaggerate the drawn shape; the saved solution is untouched",
    )
    parser.add_argument("--elevation", type=float, default=26.0)
    parser.add_argument("--azimuth", type=float, default=-62.0)
    args = parser.parse_args(argv)

    directory = Path(args.run)
    config = Config.load(directory / "config.json", validate=False)
    if args.deformation_scale is not None:
        config = replace(
            config,
            visualization=replace(
                config.visualization, mesh_deformation_scale=args.deformation_scale
            ),
        )
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    frames = summary["frames"]
    view = MeshView(
        directory,
        config,
        geometry_of(directory),
        elevation=args.elevation,
        azimuth=args.azimuth,
    )
    try:
        for index, metric in enumerate(frames):
            state = SurfaceState.load(directory / "states" / f"frame_{index:04d}.npz")
            with np.load(
                directory / "bodies" / f"frame_{index:04d}.npz", allow_pickle=False
            ) as body:
                displacement = body["gel_displacement_m"]
            view.render(index, state, metric, displacement)
            print(f"Frame {index + 1}/{len(frames)}")
    finally:
        view.close()
    if len(frames) > 1:
        save_gif(
            ImageFiles(sorted((directory / "mesh").glob("frame_*.png"))),
            directory / "mesh.gif",
            config.optics.animation_fps,
        )
    print(f"Wrote {len(frames)} finite-element views to {directory / 'mesh'}")


if __name__ == "__main__":
    main()
