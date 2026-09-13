"""CUDA projection must preserve geometry, material mapping, and force integrals."""

import os
import unittest
from dataclasses import replace

import numpy as np
from test_simulation import flat_state, full_camera

from gelsight_ansys.camera import optical_surface
from gelsight_ansys.config import Camera
from gelsight_ansys.surface import project_surface


@unittest.skipUnless(os.environ.get("GELSIGHT_TEST_CUDA") == "1", "Opt-in CUDA check")
class CudaProjectionTests(unittest.TestCase):
    def compare(self, cpu, gpu):
        self.assertEqual(cpu.keys(), gpu.keys())
        for key in cpu:
            with self.subTest(field=key):
                if cpu[key].dtype.kind in "biu":
                    np.testing.assert_array_equal(cpu[key], gpu[key])
                else:
                    np.testing.assert_allclose(
                        cpu[key],
                        gpu[key],
                        atol=1e-7 if key == "force_density_pa" else 3e-12,
                        rtol=2e-11,
                    )

    def test_arbitrary_loads_full_partial_and_empty_fov(self):
        state = flat_state()
        rng = np.random.default_rng(12)
        state.contact_force_n = rng.normal(size=state.contact_force_n.shape)
        state.contact_pressure_pa = np.arange(len(state.quads), dtype=float)
        state.contact_status = np.arange(len(state.quads)) % 4
        for camera in (
            full_camera(),
            Camera(
                width_px=19,
                height_px=11,
                fov_width_m=1.0,
                fov_height_m=1.4,
                center_x_m=0.9,
                center_y_m=0.1,
            ),
            replace(full_camera(), center_x_m=5.0),
        ):
            cpu = project_surface(state, camera)
            gpu = project_surface(state, camera, backend="cuda")
            self.compare(cpu, gpu)
            np.testing.assert_allclose(gpu["raster_force_error_n"], 0, atol=3e-12)
        full = project_surface(state, full_camera(), backend="cuda")
        np.testing.assert_allclose(
            full["fov_force_n"], state.contact_force_n.sum(axis=0), atol=3e-12
        )

    def test_deformed_surface_and_perspective_material_coordinates(self):
        state = flat_state()
        state.displacement_m[:, 0] = 0.03 + 0.04 * state.reference_m[:, 1]
        state.displacement_m[:, 1] = -0.07
        state.displacement_m[:, 2] = (
            -0.2 + 0.07 * state.reference_m[:, 0] - 0.03 * state.reference_m[:, 1]
        )
        camera = replace(
            full_camera(projection="pinhole", standoff_m=3.0),
            center_x_m=0.013,
            center_y_m=0.021,
        )
        cpu = project_surface(state, camera)
        gpu = project_surface(state, camera, backend="cuda")
        self.compare(cpu, gpu)
        self.compare(
            optical_surface(state, camera, cpu),
            optical_surface(state, camera, gpu, backend="cuda"),
        )

    def test_small_si_mesh_preserves_force_integrals(self):
        state = flat_state()
        state.reference_m *= 0.001
        xy = state.reference_m[:, :2]
        state.displacement_m[:, 2] = -0.0002 * np.exp(
            -np.sum((xy / 0.0007) ** 2, axis=1)
        )
        state.contact_force_n = (
            np.random.default_rng(4).normal(size=state.contact_force_n.shape) * 0.01
        )
        camera = replace(full_camera(), fov_width_m=0.0022, fov_height_m=0.0022)
        gpu = project_surface(state, camera, backend="cuda")
        self.compare(project_surface(state, camera), gpu)
        np.testing.assert_allclose(
            gpu["fov_force_n"], state.contact_force_n.sum(axis=0), atol=3e-12
        )
        np.testing.assert_allclose(gpu["raster_force_error_n"], 0, atol=3e-12)

    def test_invalid_geometry_is_rejected(self):
        state = flat_state()
        state.displacement_m[:, 0] = -2 * state.reference_m[:, 0]
        with self.assertRaisesRegex(ValueError, "folds"):
            project_surface(state, full_camera(), backend="cuda")
        state = flat_state()
        state.displacement_m[:, 2] = -4
        camera = full_camera(projection="pinhole", standoff_m=3)
        with self.assertRaisesRegex(ValueError, "camera plane"):
            optical_surface(state, camera, {}, backend="cuda")


if __name__ == "__main__":
    unittest.main()
