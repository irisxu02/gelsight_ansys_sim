"""Shared MAPDL process ownership, reference states, and body exports."""

import os
import re
from pathlib import Path

import numpy as np

from ..contracts import SurfaceState


def gpu_statistics(folder):
    records = []
    for path in Path(folder).iterdir():
        if path.suffix.lower() != ".dsp":
            continue
        text = path.read_text(errors="replace")
        percentages = re.findall(
            r"percentage of GPU accelerated flops\s*=\s*([\d.]+)", text, re.IGNORECASE
        )
        reported = "gpu acceleration activated" in text.lower()
        work = [float(v) for v in percentages]
        records.append(
            {
                "activation_reported": reported,
                "active": reported and any(v > 0 for v in work),
                "accelerated_flops_percent": work,
            }
        )
    return {
        "active": any(record["active"] for record in records),
        "sparse_statistics": records,
    }


def validate_solve(
    converged, output, actual_time, requested_time, load_step, expected_step
):
    """Reject unconverged states even when the result's time looks plausible."""
    if int(converged) != 1 or any(
        flag in output.lower()
        for flag in ("run continued at user request", "run terminated")
    ):
        raise RuntimeError("ANSYS reported a nonconverged nonlinear solution")
    if not np.isclose(actual_time, requested_time, rtol=1e-9, atol=1e-12):
        raise RuntimeError("ANSYS did not converge to the requested load time")
    if load_step != expected_step:
        raise RuntimeError("ANSYS load-step history was not preserved")


class AnsysSession:
    def __init__(self, config, directory, mesh, executable=None, restart=None):
        self.config = config
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mesh = mesh
        self.contact_start = len(self.mesh.hexes)
        self.object_mesh = None
        self.last_displacement = np.zeros_like(self.mesh.coordinates)
        self.object_displacement = None
        self.mapdl = None
        self.executable = executable
        self.restart = restart
        self.frame_number = restart[0] if restart else 0

    def __enter__(self):
        from ansys.mapdl.core import launch_mapdl

        cfg = self.config.solver
        root = Path(
            os.environ.get(
                f"AWP_ROOT{cfg.version}", rf"C:\Program Files\ANSYS Inc\v{cfg.version}"
            )
        )
        executable = (
            Path(self.executable)
            if self.executable
            else root / f"ansys/bin/winx64/ANSYS{cfg.version}.exe"
        )
        if not executable.is_file():
            raise FileNotFoundError(
                "ANSYS executable unavailable; supply --exec-file or AWP_ROOT for the configured version"
            )
        env = {"ANSGPU_PRINTDEVICES": "1", **getattr(self, "extra_environment", {})}
        if cfg.allow_unlisted_gpu:
            env["ANSGPU_OVERRIDE"] = "1"
        self.mapdl = launch_mapdl(
            exec_file=str(executable),
            run_location=str(self.directory),
            jobname="gel",
            nproc=cfg.cores,
            mode="grpc",
            start_instance=True,
            additional_switches="-smp -acc nvidia -na 1" if cfg.gpu else "-smp",
            license_type=cfg.license_type,
            add_env_vars=env,
            timeout=60,
            cleanup_on_exit=True,
            set_no_abort=False,
            log_apdl=str(
                self.directory
                / (
                    f"resume_{self.frame_number:04d}_commands.inp"
                    if self.restart
                    else "commands.inp"
                )
            ),
        )
        try:
            self.build()
        except BaseException:
            self.mapdl.exit()
            raise
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if self.mapdl is not None:
            self.mapdl.exit()

    def command_block(self, commands, name):
        source = "\n".join(commands) + "\n"
        (self.directory / f"{name}.inp").write_text(source, encoding="utf-8")
        result = self.mapdl.input_strings(source)
        (self.directory / f"{name}.log").write_text(str(result), encoding="utf-8")
        return result

    def reference_surface(self):
        points = self.mesh.coordinates[self.mesh.surface_nodes].copy()
        return points

    def reference_state(self):
        m = self.mesh
        count = len(m.surface_nodes)
        return SurfaceState(
            0.0,
            self.reference_surface(),
            np.zeros((count, 3)),
            m.triangles,
            m.surface_quads,
            np.zeros((count, 3)),
            np.zeros(len(m.surface_quads)),
            np.zeros(len(m.surface_quads)),
            np.zeros(len(m.surface_quads)),
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
            self.initial_pilot.copy(),
            m.surface_nodes + 1,
            "unloaded_reference",
            contact_elastic_slip_m=np.zeros((len(m.surface_quads), 4)),
            contact_integration_status=np.zeros((len(m.surface_quads), 4)),
        )

    def save_body_state(self, path, pose):
        arrays = {"gel_displacement_m": self.last_displacement}
        metric = {}
        if self.object_mesh is not None:
            arrays["indenter_displacement_m"] = self.object_displacement
            translation = np.array(
                [pose.x_m, pose.y_m, -pose.depth_m - self.config.indenter.clearance_m]
            )
            residual = self.object_displacement - translation
            metric["max_indenter_deformation_m"] = float(
                np.linalg.norm(residual, axis=1).max()
            )
            metric["indenter_grip_displacement_error_m"] = float(
                np.linalg.norm(residual[self.object_mesh.grip_nodes], axis=1).max()
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **arrays)
        return metric
