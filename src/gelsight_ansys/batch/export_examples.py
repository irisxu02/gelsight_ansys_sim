"""Export complete, passed examples with portable data and no raw solver logs."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from .presets import CASES

FILES = (
    "config.json",
    "camera.json",
    "solid_mesh.npz",
    "summary.json",
    "metrics.csv",
    "visualization.json",
    "preview.png",
    "process.gif",
    "force_curve.png",
    "report.html",
)
FOLDERS = ("states", "fields", "images", "bodies")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export_case(validation, key, output):
    records = json.loads(validation.read_text(encoding="utf-8"))
    record = records[key]
    if record["status"] != "passed":
        raise ValueError("Only passed validation cases can be exported")
    root = validation.parent.resolve()
    source = (root / record["run"]).resolve()
    source.relative_to(root)
    export_run(source, output / CASES[key])


def export_run(source, destination):
    """Export one complete, passed run as a portable example folder.

    The preset queue reaches this through its validation record; a run made
    outside the queue - a diagnostic that earned a place among the examples -
    is exported the same way, by pointing at its directory.
    """
    source, destination = Path(source).resolve(), Path(destination)
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    count = len(config["trajectory"])
    if summary["status"] != "passed" or len(summary["frames"]) != count:
        raise ValueError("A complete successful run is required")
    destination.mkdir(parents=True, exist_ok=True)
    paths = [Path(name) for name in FILES]
    plane = (
        config.get("config_kind") == "resolved_material_plane_run"
        or config.get("indenter", {}).get("shape") == "plane"
    )
    if plane:
        if not summary.get("production_mesh") or not summary.get(
            "complete_recorded_interval"
        ):
            raise ValueError(
                "Engineering pilot results cannot be exported as full examples"
            )
        paths.extend(
            Path(name) for name in ("unloaded_reference.npz", "unloaded_reference.png")
        )
        paths.extend(Path("contact") / f"frame_{i:04d}.npz" for i in range(count))
    paths.extend(
        Path(name)
        for name in ("raw_vs_subtracted.png", "raw_vs_subtracted.gif")
        if (source / name).is_file()
    )
    visualization = json.loads(
        (source / "visualization.json").read_text(encoding="utf-8")
    )
    # Historical reports used panel PNGs and always linked a tactile GIF.
    viewer_folder = visualization.get("frame_viewer_folder", "panels")
    if viewer_folder not in ("images", "panels"):
        raise ValueError("Unknown report frame folder")
    folders = (*FOLDERS, "panels") if viewer_folder == "panels" else FOLDERS
    if visualization.get("tactile_gif_saved", True):
        paths.append(Path("tactile.gif"))
    for folder in folders:
        extension = ".npz" if folder in ("states", "fields", "bodies") else ".png"
        paths.extend(Path(folder) / f"frame_{i:04d}{extension}" for i in range(count))
        if folder == "fields":
            paths.extend(Path(folder) / f"frame_{i:04d}.json" for i in range(count))
    if config["optics"].get("background_image"):
        paths.append(Path("background.png"))
    for relative in paths:
        if not (source / relative).is_file():
            raise FileNotFoundError(f"Incomplete run: missing {relative}")
    manifest = {
        "schema_version": 1,
        "frame_count": count,
        "sensor_model": config.get("sensor_model"),
        "files": {},
    }
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
        checksum = digest(target)
        if checksum != digest(source / relative):
            raise RuntimeError(f"Copy verification failed: {relative}")
        manifest["files"][relative.as_posix()] = {
            "bytes": target.stat().st_size,
            "sha256": checksum,
        }
    # Remove stale generated frames when replacing a longer old trajectory.
    wanted = {p.as_posix() for p in paths}
    for folder in (*FOLDERS, "panels"):
        for path in (destination / folder).glob("frame_*"):
            if path.is_file() and path.relative_to(destination).as_posix() not in wanted:
                path.unlink()
    for name in ("preview.gif", "tactile.gif"):
        if name not in wanted:
            (destination / name).unlink(missing_ok=True)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {destination.name}: {count} frames, {len(paths)} checked files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validation",
        type=Path,
        required=True,
        help="Completed integration validation.json",
    )
    parser.add_argument("--output", type=Path, default=Path("docs/examples"))
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    args = parser.parse_args()
    for key in CASES if args.case == "all" else (args.case,):
        export_case(args.validation, key, args.output)


if __name__ == "__main__":
    main()
