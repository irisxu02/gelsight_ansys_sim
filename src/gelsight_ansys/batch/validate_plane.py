"""Run and validate one full material-plane recording before public export."""

import argparse
import json
from pathlib import Path

from gelsight_ansys.artifacts import write_json
from gelsight_ansys.config import Config
from gelsight_ansys.pipeline import resume_run, run
from gelsight_ansys.run_contract import require_dataset


def resumable(candidates, config, progress=print):
    """The furthest interrupted run among these that this config can continue.

    A plane slide is hours of solver time, and an interruption - a failure, a
    stopped queue, a fix to the code that reads its results - used to mean all
    of it again from the preload. The restart rules decide what may be
    continued; everything they refuse falls back to a fresh run, with the
    reason said out loud rather than silently redone.
    """
    from gelsight_ansys.plane_restart import validate_plane_resume

    best, reached = None, 0
    for directory in sorted(Path(candidates).glob(f"{config.name}_*")):
        summary = directory / "summary.json"
        if not summary.is_file():
            continue
        frames = len(json.loads(summary.read_text()).get("frames", []))
        if frames <= reached:
            continue
        try:
            validate_plane_resume(
                directory, config, from_checkpoint=(directory / "solver/gel.mntr").is_file()
            )
        except (ValueError, FileNotFoundError, KeyError) as refused:
            progress(f"Cannot continue {directory.name}: {refused}")
            continue
        best, reached = directory, frames
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--render-scale", type=int, default=4)
    parser.add_argument("--libraries", type=Path)
    parser.add_argument("--exec-file", type=Path)
    parser.add_argument(
        "--resume-from",
        type=Path,
        help="Directory of earlier runs of this preset; the furthest one that "
        "the restart rules accept is continued instead of solving again",
    )
    args = parser.parse_args(argv)
    config = Config.load(args.config).with_render_scale(args.render_scale)
    if not config.is_plane:
        raise ValueError("This validation contract requires plane geometry")
    source = (
        resumable(args.resume_from, config)
        if args.resume_from and Path(args.resume_from).is_dir()
        else None
    )
    if source is not None:
        frames = len(json.loads((source / "summary.json").read_text())["frames"])
        print(f"Continuing {source.name} from {frames} saved frames", flush=True)
        directory, summary = resume_run(
            source,
            args.output,
            args.exec_file,
            config=config,
            libraries=args.libraries,
            from_checkpoint=(source / "solver/gel.mntr").is_file(),
        )
    else:
        directory, summary = run(
            config,
            args.output,
            args.exec_file,
            args.config.parent,
            libraries=args.libraries,
        )
    require_dataset(summary, config.to_dict(), config.name)
    checks = [m["contact_coverage"] for m in summary["recorded_substeps"]]
    record = {
        "status": "passed",
        "run": directory.name,
        "frame_count": len(summary["frames"]),
        "minimum_active_bin_fraction": min(m["active_bin_fraction"] for m in checks),
        "minimum_plane_edge_margin_m": min(
            m["minimum_plane_edge_margin_m"] for m in checks
        ),
        "peak_force_n": max(m["normal_force_n"] for m in summary["frames"]),
        "elapsed_s": summary["elapsed_s"],
        "render_device": summary["render_device"],
        "projection_device": summary["projection_device"],
        "gpu_mechanics_verified": summary["gpu_mechanics_verified"],
    }
    write_json(args.output / "validation.json", {config.name: record})
    print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    main()
