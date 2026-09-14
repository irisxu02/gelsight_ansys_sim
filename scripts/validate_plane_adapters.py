"""Licensed element coupons for plane material adapters; private outputs only."""

import argparse
import ctypes
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gelsight_ansys.ansys.materials import (
    bulk_commands,
    fabric_properties,
)
from gelsight_ansys.ansys.session import AnsysSession, validate_solve
from gelsight_ansys.config import Config
from gelsight_ansys.mesh import tensor_mesh
from gelsight_ansys.plane_config import PlaneCase


class Coupon(AnsysSession):
    def __init__(self, case, directory, libraries, relaxation=True):
        config = Config()
        super().__init__(
            replace(config, solver=replace(config.solver, force_tolerance=1e-7)),
            directory,
            tensor_mesh(*([np.linspace(0, 0.001, 3)] * 3)),
        )
        self.case = case
        self.libraries = Path(libraries).resolve()
        self.relaxation = relaxation

    def __enter__(self):
        # The parent launcher forwards only explicitly declared process variables.
        self.extra_environment = {"ANS_USER_PATH": str(self.libraries)}
        return super().__enter__()

    def build(self):
        m = self.mesh
        material = dict(self.case.bulk)
        if not self.relaxation:
            material.pop("viscoelasticity", None)
        commands = [
            "/PREP7",
            "ET,1,SOLID185",
            f"KEYOPT,1,6,{int(material.get('formulation') == 'mixed_up')}",
        ]
        commands += bulk_commands(material, 1)
        commands += ["TYPE,1", "MAT,1"]
        commands += [
            f"N,{i + 1},{x:.16g},{y:.16g},{z:.16g}"
            for i, (x, y, z) in enumerate(m.coordinates)
        ]
        commands += [
            f"EN,{i + 1}," + ",".join(str(int(n) + 1) for n in e)
            for i, e in enumerate(m.hexes)
        ]
        self.boundary = np.flatnonzero(
            np.any((m.coordinates == 0) | (m.coordinates == 0.001), axis=1)
        )
        for n in self.boundary:
            commands.append(f"D,{n + 1},ALL,0")
        commands += [
            "FINISH",
            "/SOLU",
            "ANTYPE,STATIC",
            "NLGEOM,ON",
            "NROPT,UNSYM",
            "EQSLV,SPARSE",
            "AUTOTS,ON",
            "NEQIT,80",
            "CNVTOL,F,,1e-7,2,1e-8",
            "OUTRES,ALL,ALL",
            "RESCONTROL,DEFINE,ALL,LAST",
            "FINISH",
        ]
        self.command_block(commands, "coupon_model")
        self.step = 0

    def deform(self, F, at_time, face_axis=2):
        self.step += 1
        commands = ["FINISH", "/SOLU"]
        if self.step > 1:
            commands.append("ANTYPE,,REST")
        commands += [f"TIME,{at_time:.16g}", "NSUBST,2,100,1"]
        delta = self.mesh.coordinates @ (F - np.eye(3)).T
        for n in self.boundary:
            commands += [
                f"D,{n + 1},{dof},{delta[n, j]:.16g}"
                for j, dof in enumerate(("UX", "UY", "UZ"))
            ]
        commands += [
            "NCNV,2",
            "SOLVE",
            "*GET,CNV,ACTIVE,0,SOLU,CNVG",
            "FINISH",
            "/POST1",
            "SET,LAST",
        ]
        output = self.command_block(commands, f"coupon_step_{self.step}")
        validate_solve(
            self.mapdl.parameters["CNV"],
            str(output),
            self.mapdl.get_value("ACTIVE", 0, "SET", "TIME"),
            at_time,
            int(self.mapdl.get_value("ACTIVE", 0, "SET", "LSTP")),
            self.step,
        )
        top = np.flatnonzero(self.mesh.coordinates[:, face_axis] == 0.001)
        commands = ["*DEL,RFORCE", f"*DIM,RFORCE,ARRAY,{len(top)},3"]
        for i, n in enumerate(top):
            commands += [
                f"*GET,RFORCE({i + 1},{j + 1}),NODE,{n + 1},RF,{dof}"
                for j, dof in enumerate(("FX", "FY", "FZ"))
            ]
        self.command_block(commands, f"coupon_force_{self.step}")
        return np.array(self.mapdl.parameters["RFORCE"]).sum(axis=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libraries", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wait-for-queue", type=Path)
    parser.add_argument("--case", choices=("all", "fluffy_fabric"), default="all")
    args = parser.parse_args()
    if args.wait_for_queue:
        while json.loads(args.wait_for_queue.read_text()).get("status") == "running":
            print(
                "Waiting for the current example queue to release the solver license.",
                flush=True,
            )
            time.sleep(20)
    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in (
        ("soft_rubber", "compressible_foam", "fluffy_fabric")
        if args.case == "all"
        else (args.case,)
    ):
        print("Testing", name, flush=True)
        case = PlaneCase.load(ROOT / f"configs/material_plane_slide/{name}.json")
        F = np.diag([1.02, 1.01, 0.9])
        with Coupon(case, args.output / name, args.libraries) as model:
            initial = model.deform(F, 1e-6)
            held = model.deform(F, 2.000001)
        if not (initial[2] < 0 and held[2] < 0 and abs(held[2]) < abs(initial[2])):
            raise RuntimeError(f"{name}: compressive force or stress relaxation failed")
        result = {
            "initial_force_n": initial.tolist(),
            "held_force_n": held.tolist(),
            "relaxation_verified": True,
        }
        if name == "fluffy_fabric":
            # Load dependencies by using the installed ANSYS binary directory.
            import os

            root = Path(os.environ.get("AWP_ROOT252", r"C:\Program Files\ANSYS Inc\v252"))
            with os.add_dll_directory(str(root / "ansys/bin/winx64")):
                lib = ctypes.CDLL(str(args.libraries.resolve() / "UserHyperAnisoLib.dll"))
            arr = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
            fn = lib.gel_fabric_energy
            fn.argtypes = [arr] * 4
            fn.restype = ctypes.c_double
            C = F.T @ F
            g, H = np.zeros(6), np.zeros((6, 6))
            fn(
                np.array([C[0, 0], C[1, 1], C[2, 2], C[0, 1], C[0, 2], C[1, 2]]),
                fabric_properties(case.bulk),
                g,
                H,
            )
            dWdC = np.diag(g[:3])
            for k, (i, j) in enumerate(((0, 1), (0, 2), (1, 2))):
                dWdC[i, j] = dWdC[j, i] = g[3 + k] / 2
            expected = (2 * F @ dWdC)[:, 2] * 1e-6
            np.testing.assert_allclose(initial, expected, rtol=2e-5, atol=1e-8)
            result["analytic_initial_force_n"] = expected.tolist()
            shear_results = []
            for axis, direction in ((0, 1), (0, 2), (1, 2)):
                F = np.eye(3)
                F[axis, direction] = 0.02
                with Coupon(
                    case,
                    args.output / f"fabric_shear_{axis}_{direction}",
                    args.libraries,
                    relaxation=False,
                ) as model:
                    measured = model.deform(F, 0.01, face_axis=direction)
                C = F.T @ F
                fn(
                    np.array([C[0, 0], C[1, 1], C[2, 2], C[0, 1], C[0, 2], C[1, 2]]),
                    fabric_properties(case.bulk),
                    g,
                    H,
                )
                dWdC = np.diag(g[:3])
                for k, (i, j) in enumerate(((0, 1), (0, 2), (1, 2))):
                    dWdC[i, j] = dWdC[j, i] = g[3 + k] / 2
                expected = (2 * F @ dWdC)[:, direction] * 1e-6
                np.testing.assert_allclose(measured, expected, rtol=2e-5, atol=1e-8)
                shear_results.append(
                    {
                        "shear_axes": [axis, direction],
                        "measured_force_n": measured.tolist(),
                        "analytic_force_n": expected.tolist(),
                    }
                )
            result["shear_coupons"] = shear_results
        results[name] = result
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    print("Material coupons passed.", flush=True)


if __name__ == "__main__":
    main()
