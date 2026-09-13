"""Reference-image preservation, signed subtraction, and optical GPU agreement."""

import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_simulation import flat_state

from gelsight_ansys.config import Camera, Config
from gelsight_ansys.optics import Renderer
from gelsight_ansys.surface import project_surface
from gelsight_ansys.taxim import ASSETS


class RawOpticsTests(unittest.TestCase):
    def setup_scene(self, mode="raw", backend="cpu"):
        c = Config()
        c = replace(
            c,
            camera=Camera(
                width_px=320, height_px=240, fov_width_m=1.8, fov_height_m=1.4
            ),
            optics=replace(c.optics, backend=backend, render_mode=mode),
        )
        return c, project_surface(flat_state(), c.camera)

    def test_unpressed_rgb_preserves_measured_background_orientation(self):
        c, fields = self.setup_scene()
        actual = Renderer(c).render(fields, np.empty((0, 2)))
        with Image.open(ASSETS / "background.png") as image:
            expected = np.rot90(np.asarray(image), k=-1)
        np.testing.assert_array_equal(actual, expected)
        self.assertGreater(np.std(actual[..., 1]), 5)

    def test_infinitesimal_tilt_does_not_create_a_color_boundary(self):
        c, fields = self.setup_scene()
        renderer = Renderer(c)
        base = renderer.render(fields, np.empty((0, 2)))
        for direction in np.linspace(-np.pi, np.pi, 17):
            n = np.array(
                [1e-6 * np.cos(direction), 1e-6 * np.sin(direction), np.sqrt(1 - 1e-12)]
            )
            fields["normals"][:] = n
            tilted = renderer.render(fields, np.empty((0, 2)))
            self.assertLessEqual(np.abs(tilted.astype(int) - base.astype(int)).max(), 1)

    def test_response_has_continuous_gradient_at_angular_bins(self):
        from gelsight_ansys.taxim import TaximResponse, evaluate

        c, _ = self.setup_scene()
        response = TaximResponse(c.camera, 2, 2)
        feature = response.features[120, 160]

        def value(tilt, azimuth):
            n = np.array(
                [
                    np.sin(tilt) * np.cos(azimuth),
                    np.sin(tilt) * np.sin(azimuth),
                    np.cos(tilt),
                ]
            )
            return evaluate(response.table, feature, n)

        eps = 1e-7
        tilt = 17 * np.pi / (2 * 119)
        center = value(tilt, 0.37)
        left = (center - value(tilt - eps, 0.37)) / eps
        right = (value(tilt + eps, 0.37) - center) / eps
        np.testing.assert_allclose(left, right, atol=0.05, rtol=1e-4)
        center = value(tilt, -np.pi)
        left = (center - value(tilt, np.pi - eps)) / eps
        right = (value(tilt, -np.pi + eps) - center) / eps
        np.testing.assert_allclose(left, right, atol=0.05, rtol=1e-4)

    def test_difference_keeps_both_signs_marker_motion_and_release(self):
        c, fields = self.setup_scene("subtracted")
        renderer = Renderer(c)
        original = np.array([[100.0, 100.0]])
        moved = np.array([[125.0, 100.0]])
        first = renderer.render(fields, original, original)
        np.testing.assert_array_equal(first, 128)
        frame = renderer.render(fields, moved, moved)
        self.assertGreater(renderer.difference[100, 100].min(), 0)
        self.assertLess(renderer.difference[100, 125].max(), 0)
        self.assertEqual(renderer.difference.dtype, np.int16)
        self.assertGreater(frame[100, 100].min(), 128)
        self.assertLess(frame[100, 125].max(), 128)
        released = renderer.render(fields, original, original)
        np.testing.assert_array_equal(released, first)

    @unittest.skipUnless(
        os.environ.get("GELSIGHT_TEST_CUDA") == "1", "Opt-in CUDA test"
    )
    def test_cuda_raw_and_subtracted_match_cpu_with_surface_tilt(self):
        c, fields = self.setup_scene()
        marker = np.array([[90.0, 90.0]])
        for mode in ("raw", "subtracted"):
            cpu = Renderer(replace(c, optics=replace(c.optics, render_mode=mode)))
            gpu = Renderer(
                replace(c, optics=replace(c.optics, render_mode=mode, backend="cuda"))
            )
            cpu.render(fields, marker, marker)
            gpu.render(fields, marker, marker)
            deformed = {k: v.copy() for k, v in fields.items()}
            yy, xx = np.indices(fields["valid_mask"].shape)
            nx = 0.3 * np.sin(xx / 23)
            ny = 0.2 * np.cos(yy / 29)
            deformed["normals"] = np.stack(
                (nx, ny, np.sqrt(1 - nx * nx - ny * ny)), axis=-1
            )
            a = cpu.render(deformed, marker, marker)
            b = gpu.render(deformed, marker, marker)
            self.assertLessEqual(np.max(np.abs(a.astype(int) - b.astype(int))), 1)
            self.assertGreater(np.abs(cpu.difference).max(), 10)


if __name__ == "__main__":
    unittest.main()
