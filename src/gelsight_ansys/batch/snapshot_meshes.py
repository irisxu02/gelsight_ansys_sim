"""Embed imported object meshes before a detached batch snapshot is launched."""

import argparse
import json
from pathlib import Path

from gelsight_ansys.config import Config


def freeze_mesh_inputs(source, snapshot):
    source, snapshot = Path(source), Path(snapshot)
    frozen = []
    for path in sorted((source / "configs").rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("config_kind") != "contact_simulation"
            or data.get("object", {}).get("geometry", {}).get("shape") != "mesh"
        ):
            continue
        # Resolve against the original config location, including external assets.
        config = Config.load(path)
        relative = path.relative_to(source)
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        frozen.append(relative.as_posix())
    return frozen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        f"Embedded mesh data in {len(freeze_mesh_inputs(args.source, args.snapshot))} snapshot configs"
    )
    return 0


if __name__ == "__main__":
    main()
