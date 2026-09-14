"""Run and validate one full material-plane recording before public export."""

import argparse
import json
from pathlib import Path

from gelsight_ansys.artifacts import write_json
from gelsight_ansys.config import Config
from gelsight_ansys.pipeline import run
from gelsight_ansys.run_contract import require_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--render-scale", type=int, default=4)
    parser.add_argument("--libraries", type=Path)
    parser.add_argument("--exec-file", type=Path)
    args = parser.parse_args(argv)
    config = Config.load(args.config).with_render_scale(args.render_scale)
    if not config.is_plane:
        raise ValueError("This validation contract requires plane geometry")
    directory, summary = run(
        config, args.output, args.exec_file, args.config.parent, libraries=args.libraries
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
