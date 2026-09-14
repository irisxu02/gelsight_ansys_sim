"""Launch a fresh MAPDL instance, compress an elastic cube, verify and export.

Run with Windows Python when using the Windows ANSYS installation. This is
an API/solver smoke test, not a calibrated gel or contact simulation. SI units.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from .private import exception_details


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(os.environ.get("AWP_ROOT252", r"C:\Program Files\ANSYS Inc\v252"))
    parser.add_argument(
        "--exec-file", type=Path, default=root / "ansys/bin/winx64/ANSYS252.exe"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/smoke",
    )
    parser.add_argument(
        "--license-type", help="Optional installed MAPDL license product"
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Explicit CPU diagnostic run; default requests NVIDIA GPU",
    )
    parser.add_argument(
        "--include-private-diagnostics",
        action="store_true",
        help="Include local paths and full exception traces in the summary",
    )
    args = parser.parse_args(argv)
    out = args.output.resolve() / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "failed",
        "python": platform.python_version(),
        "run_id": out.name,
        "units": "m,N,Pa",
        "gpu_requested": not args.cpu,
        "gpu_solver_use_verified": False,
    }
    if args.include_private_diagnostics:
        report.update(
            executable=sys.executable,
            ansys_executable=str(args.exec_file),
            output=str(out),
            license_type=args.license_type,
        )
    mapdl = None
    try:
        if not args.exec_file.is_file():
            raise FileNotFoundError(f"ANSYS executable not found: {args.exec_file}")
        if os.name != "nt" and args.exec_file.suffix.lower() == ".exe":
            raise RuntimeError("Use Windows Python to launch the Windows ANSYS solver.")
        import numpy as np
        from ansys.mapdl.core import launch_mapdl

        report["pymapdl_version"] = importlib.metadata.version("ansys-mapdl-core")
        options = dict(
            exec_file=str(args.exec_file),
            run_location=str(out),
            jobname="smoke",
            nproc=2,
            mode="grpc",
            start_instance=True,
            additional_switches="-smp" if args.cpu else "-smp -acc nvidia -na 1",
            timeout=30,
            add_env_vars={"ANSGPU_PRINTDEVICES": "1"},
            cleanup_on_exit=True,
            log_apdl=str(out / "commands.inp"),
        )
        if args.license_type:
            options["license_type"] = args.license_type
        print(f"Launching MAPDL; run: {out.name}", flush=True)
        mapdl = launch_mapdl(**options)
        report["mapdl_version"] = str(mapdl.version)
        if args.include_private_diagnostics:
            report["server"] = str(mapdl)
        mapdl.prep7()
        mapdl.et(1, "SOLID185")
        young, poisson, length, depth = 1.0e6, 0.3, 0.01, 0.0001
        mapdl.mp("EX", 1, young)
        mapdl.mp("PRXY", 1, poisson)
        mapdl.block(0, length, 0, length, 0, length)
        mapdl.esize(length / 2)
        mapdl.mshape(0, "3D")
        mapdl.mshkey(1)
        mapdl.vmesh("ALL")
        # Symmetry constraints allow free lateral Poisson expansion.
        for axis, dof in (("X", "UX"), ("Y", "UY"), ("Z", "UZ")):
            mapdl.nsel("S", "LOC", axis, 0)
            mapdl.d("ALL", dof, 0)
        mapdl.nsel("S", "LOC", "Z", length)
        mapdl.d("ALL", "UZ", -depth)
        top_ids = mapdl.mesh.nnum.copy()
        mapdl.allsel()
        mapdl.finish()
        mapdl.slashsolu()
        mapdl.antype("STATIC")
        mapdl.eqslv("SPARSE")
        mapdl.outres("ALL", "ALL")
        (out / "solve.txt").write_text(mapdl.solve(), encoding="utf-8")
        mapdl.finish()
        mapdl.post1()
        mapdl.set("LAST")
        node_ids = mapdl.mesh.nnum.copy()
        xyz = mapdl.mesh.nodes.copy()
        displacement = mapdl.post_processing.nodal_displacement("ALL")
        reaction = sum(
            float(mapdl.get_value("NODE", int(n), "RF", "FZ")) for n in top_ids
        )
        expected_force = -young * length**2 * depth / length
        expected_u = np.column_stack(
            (
                poisson * depth / length * xyz[:, 0],
                poisson * depth / length * xyz[:, 1],
                -depth / length * xyz[:, 2],
            )
        )
        force_error = abs(reaction - expected_force) / abs(expected_force)
        displacement_error = float(np.max(np.abs(displacement - expected_u)))
        np.savetxt(
            out / "nodes.csv",
            np.column_stack((node_ids, xyz, displacement)),
            delimiter=",",
            header="node_id,x_m,y_m,z_m,ux_m,uy_m,uz_m",
            comments="",
        )
        report.update(
            node_count=len(node_ids),
            element_count=int(mapdl.mesh.n_elem),
            expected_top_reaction_n=expected_force,
            top_reaction_n=reaction,
            force_relative_error=force_error,
            max_displacement_error_m=displacement_error,
            force_relative_tolerance=1e-5,
            displacement_tolerance_m=1e-9,
        )
        if not np.isfinite(force_error) or force_error > 1e-5:
            raise AssertionError(f"Reaction differs from E*A*strain: {force_error}")
        if not np.isfinite(displacement_error) or displacement_error > 1e-9:
            raise AssertionError(
                f"Displacement differs from uniaxial solution: {displacement_error}"
            )
        # A requested accelerator is not proof it was used. ANSYS documents
        # this exact marker in sparse solver statistics when GPU work occurred.
        gpu_lines = []
        for stats in out.glob("*.dsp"):
            gpu_lines.extend(
                line.strip()
                for line in stats.read_text(errors="replace").splitlines()
                if "gpu acceleration activated" in line.lower()
            )
        report["gpu_evidence"] = gpu_lines
        report["gpu_solver_use_verified"] = bool(gpu_lines)
        report["numerical_checks_passed"] = True
        if not args.cpu and not gpu_lines:
            raise RuntimeError(
                "Numerical checks passed, but GPU use was not confirmed in .dsp statistics. "
                "Inspect solver logs; a tiny mesh may not offload GPU work."
            )
        report["status"] = "passed"
    except Exception as error:
        report.update(exception_details(error, args.include_private_diagnostics))
    finally:
        if mapdl is not None:
            try:
                mapdl.exit()
            except Exception as error:
                report["cleanup_error"] = exception_details(
                    error, args.include_private_diagnostics
                )
                report["status"] = "failed"
        (out / "summary.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1
