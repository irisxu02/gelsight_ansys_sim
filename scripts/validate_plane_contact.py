"""Licensed contact-law coupons with prescribed geometry and exact force checks."""

import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gelsight_ansys.ansys.session import validate_solve
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_mechanics import AnsysPlane
from gelsight_ansys.rst_contact import ContactResult
from gelsight_ansys.simulation_config import config_for_plane


class ContactCoupon(AnsysPlane):
    def build(self):
        super().build()
        commands = [
            "FINISH",
            "/PREP7",
            f"NSEL,S,NODE,,1,{len(self.mesh.coordinates)}",
            "D,ALL,ALL,0",
            "ALLSEL,ALL",
        ]
        # Keep the interior gel nodes free so a finite equilibrium system remains.
        xyz = self.mesh.coordinates
        interior = np.all(
            (xyz > xyz.min(axis=0) + 1e-12) & (xyz < xyz.max(axis=0) - 1e-12), axis=1
        )
        commands.extend(f"DDELE,{i + 1},ALL" for i in np.flatnonzero(interior))
        commands += [
            "RMODIF,1,3,-1e9",
            "FINISH",
            "/SOLU",
            "CNVTOL,F,,1e-7,2,1e-8",
            "PRED,OFF",
            "FINISH",
        ]
        self.command_block(commands, "coupon_fixture")
        self.step = 0

    def at(self, gap, x, t):
        self.step += 1
        commands = ["FINISH", "/SOLU"]
        if self.step > 1:
            commands += ["ANTYPE,,REST"]
        commands += [
            f"TIME,{t}",
            "NSUBST,1,100,1",
            f"D,{self.pilot},UZ,{gap}",
            f"D,{self.pilot},UX,{x}",
            "NCNV,2",
            "SOLVE",
            "*GET,CNV,ACTIVE,0,SOLU,CNVG",
            "FINISH",
            "/POST1",
            "SET,LAST",
        ]
        self.mapdl.ignore_errors = True
        result = self.command_block(commands, f"coupon_{self.step:02d}")
        self.mapdl.ignore_errors = False
        validate_solve(
            self.mapdl.parameters["CNV"],
            str(result),
            self.mapdl.get_value("ACTIVE", 0, "SET", "TIME"),
            t,
            int(self.mapdl.get_value("ACTIVE", 0, "SET", "LSTP")),
            self.step,
        )
        reader = ContactResult(
            self.directory / "gel.rst",
            self.contact_start + 1,
            len(self.mesh.surface_quads),
            24,
            self.nonmisc_base,
        )
        data = reader.records(reader.result.nsets - 1)
        details = reader.details(data)
        force = reader.contact_forces(
            data, self.mesh.surface_quads, len(self.mesh.surface_nodes)
        ).sum(axis=0)
        return force, details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libraries", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    case = copy.deepcopy(
        PlaneCase.load(ROOT / "configs/material_plane_slide/sticky_surface.json")
    )
    case.suite["sensor"]["gel"].update(width_m=0.002, length_m=0.002, thickness_m=0.001)
    case.suite["specimen"].update(width_m=0.006, length_m=0.006, thickness_m=0.001)
    config = config_for_plane(case, element_size_m=0.0005)
    records = []
    with ContactCoupon(
        config, args.output / "solver", libraries=args.libraries
    ) as model:
        samples = [
            (40e-6, 0),
            (20e-6, 0),
            (10e-6, 0),
            (0, 0),
            (-0.5e-6, 0),
            (-0.5e-6, 20e-6),
            (40e-6, 20e-6),
            (10e-6, 20e-6),
            (0, 20e-6),
        ]
        for i, (gap, x) in enumerate(samples):
            force, details = model.at(gap, x, (i + 1) * 0.01)
            attached = np.clip(1 - gap / 20e-6, 0, 1)
            pressure = max(0, -1e9 * gap)
            expected = (pressure - 5000 * attached) * 0.002**2
            np.testing.assert_allclose(-force[2], expected, atol=2e-7, rtol=2e-5)
            if i == 5:
                tau = np.linalg.norm(force[:2]) / 0.002**2
                assert (
                    0.45 * pressure + 3000 - 1e-3 <= tau <= 0.6 * pressure + 3000 + 1e-3
                )
                assert np.all(details["status"] == 2)
            record = {
                "gap_m": gap,
                "x_m": x,
                "force_on_gel_n": force.tolist(),
                "expected_normal_force_n": expected,
                "statuses": np.unique(details["status"]).tolist(),
            }
            records.append(record)
            print(json.dumps(record), flush=True)
    (args.output / "results.json").write_text(
        json.dumps({"status": "passed", "samples": records}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
