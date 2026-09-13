"""Small licensed plane-contact probe; never exported as a production example."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_mechanics import AnsysPlane
from gelsight_ansys.simulation_config import config_for_plane


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="rigid_reference")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--libraries", type=Path)
    parser.add_argument("--diagnostic-rigid-material", action="store_true")
    parser.add_argument("--diagnostic-native-friction", action="store_true")
    args = parser.parse_args()
    case = PlaneCase.load(ROOT / f"configs/material_plane_slide/{args.case}.json")
    if args.diagnostic_rigid_material:
        case.case["bulk_material"] = {"model": "rigid"}
    if args.diagnostic_native_friction:
        law = case.case["contact"]["friction"]
        case.case["contact"]["friction"] = {**law["x"], "decay_velocity_m_s": law["decay_velocity_m_s"]}
    config = config_for_plane(case, element_size_m=0.002)
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    with AnsysPlane(config, args.output / "solver", libraries=args.libraries) as model:
        for target in (-1.99, -1.98):
            for state, pose, gpu in model.solve_interval(config.physical_pose(target)):
                record = {
                    "time_s": pose.time_s,
                    "normal_force_n": float(-state.contact_force_n[:, 2].sum()),
                    "balance_error_n": float(
                        np.linalg.norm(
                            state.contact_force_n.sum(axis=0) + state.backing_reaction_n
                        )
                    ),
                    "minimum_pressure_pa": float(
                        model.contact_details["pressure"].min()
                    ),
                    "maximum_pressure_pa": float(
                        model.contact_details["pressure"].max()
                    ),
                    "statuses": np.unique(model.contact_details["status"]).tolist(),
                }
                records.append(record)
                print(json.dumps(record), flush=True)
    (args.output / "results.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
