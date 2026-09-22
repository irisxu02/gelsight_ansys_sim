"""Independent camera geometry and deformable-marker checks."""

import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_simulation import flat_state

from gelsight_ansys.camera import (
    optical_surface,
    pixels_to_reference,
    reference_marker_pixels,
)
from gelsight_ansys.config import Camera, Config
from gelsight_ansys.optics import Renderer
from gelsight_ansys.surface import Markers, image_coordinates, project_surface


class CameraTests(unittest.TestCase):
    def test_pinhole_projection_and_inverse_material_attachment(self):
        state = flat_state()
        camera = Camera(
            width_px=81,
            height_px=61,
            fov_width_m=1.4,
            fov_height_m=1.0,
            projection="pinhole",
            standoff_m=3.0,
        )
        state.displacement_m[:, 0] = 0.1 + 0.2 * state.reference_m[:, 0]
        state.displacement_m[:, 2] = -0.4 + 0.1 * state.reference_m[:, 1]
        force = project_surface(state, camera)
        original_force = force["pixel_force_n"].copy()
        optical = optical_surface(state, camera, force)
        valid = optical["optical_valid_mask"]
        self.assertTrue(valid.all())
        rr, cc = np.indices(valid.shape)
        image = image_coordinates(optical["optical_position_m"][valid], camera)
        np.testing.assert_allclose(
            image, np.column_stack((cc[valid], rr[valid])), atol=1e-12
        )
        ref = optical["optical_reference_m"][valid]
        xyz = optical["optical_position_m"][valid]
        np.testing.assert_allclose(xyz[:, 0], 1.2 * ref[:, 0] + 0.1, atol=1e-14)
        np.testing.assert_allclose(xyz[:, 2], -0.4 + 0.1 * ref[:, 1], atol=1e-14)
        np.testing.assert_array_equal(force["pixel_force_n"], original_force)
        center = np.array([(camera.width_px - 1) / 2, (camera.height_px - 1) / 2])
        rest = np.array([[0.3, 0.2, 0.0]])
        pressed = rest.copy()
        pressed[:, 2] = -0.5
        np.testing.assert_allclose(
            image_coordinates(pressed, camera) - center,
            (image_coordinates(rest, camera) - center) * 3 / 2.5,
        )

    def test_sensor_lattice_round_trip_and_centering(self):
        base = Config()
        camera = Camera(width_px=320, height_px=240, fov_width_m=1.8, fov_height_m=1.4)
        optics = replace(
            base.optics, marker_grid_rows_cols=(7, 9), marker_margin_px=(24, 24)
        )
        pixels = reference_marker_pixels(camera, optics)
        self.assertEqual(pixels.shape, (63, 2))
        np.testing.assert_allclose(pixels.mean(axis=0), [159.5, 119.5])
        np.testing.assert_allclose(
            image_coordinates(pixels_to_reference(pixels, camera), camera),
            pixels,
            atol=1e-13,
        )
        markers = Markers(flat_state(), 0.4, camera, optics)
        np.testing.assert_allclose(
            image_coordinates(markers.reference_m, camera), pixels, atol=1e-13
        )

    def test_marker_offset_shifts_the_lattice_and_scales_with_resolution(self):
        base = Config()
        optics = replace(
            base.optics, marker_grid_rows_cols=(11, 17), marker_margin_px=(46.3, 42.9)
        )
        centered = reference_marker_pixels(base.camera, optics)
        moved = replace(optics, marker_offset_px=(5.2, 4.5))
        np.testing.assert_allclose(
            reference_marker_pixels(base.camera, moved) - centered,
            np.tile([4.5, 5.2], (len(centered), 1)),
        )
        config = replace(base, optics=moved).validate()
        scaled = config.with_render_scale(4)
        self.assertEqual(scaled.optics.marker_offset_px, (20.8, 18.0))
        np.testing.assert_allclose(
            pixels_to_reference(
                reference_marker_pixels(scaled.camera, scaled.optics), scaled.camera
            ),
            pixels_to_reference(reference_marker_pixels(base.camera, moved), base.camera),
            atol=1e-12,
        )
        with self.assertRaisesRegex(ValueError, "Offset marker lattice"):
            replace(base, optics=replace(moved, marker_offset_px=(0.0, 43.0))).validate()

    def render_stretch(self, backend, stretch):
        state = flat_state()
        base = Config()
        camera = Camera(width_px=101, height_px=101, fov_width_m=1.8, fov_height_m=1.8)
        config = replace(
            base,
            camera=camera,
            optics=replace(
                base.optics,
                model="analytic",
                backend=backend,
                marker_style="material",
                marker_radius_px=7,
                marker_opacity=0.45,
            ),
        )
        state.displacement_m[:, 0] = (stretch - 1) * state.reference_m[:, 0]
        fields = project_surface(state, camera)
        fields.update(optical_surface(state, camera, fields))
        return Renderer(config).render(
            fields, np.array([[50.0, 50.0]]), np.array([[50.0, 50.0]])
        )

    def test_dot_texture_stretches_with_material(self):
        rest = self.render_stretch("cpu", 1.0)
        stretched = self.render_stretch("cpu", 1.5)
        bg = rest[0, 0].astype(float)
        mass = (bg - rest).sum()
        ratio = (bg - stretched).sum() / mass
        self.assertAlmostEqual(ratio, 1.5, delta=0.03)
        self.assertGreater(
            np.count_nonzero(stretched[50, :, 0] < bg[0]),
            np.count_nonzero(rest[50, :, 0] < bg[0]),
        )
        self.assertEqual(
            np.count_nonzero(stretched[:, 50, 0] < bg[0]),
            np.count_nonzero(rest[:, 50, 0] < bg[0]),
        )

    def test_projected_marker_center_matches_rendered_dot(self):
        state = flat_state()
        base = Config()
        camera = Camera(
            width_px=151,
            height_px=121,
            fov_width_m=1.8,
            fov_height_m=1.4,
            projection="pinhole",
            standoff_m=3.0,
        )
        config = replace(
            base,
            camera=camera,
            optics=replace(
                base.optics,
                model="analytic",
                backend="cpu",
                marker_style="material",
                marker_radius_px=5,
                marker_opacity=0.45,
            ),
        )
        reference = np.array([[0.3, 0.1, 0.0]])
        shift = np.array([0.1, -0.06, -0.4])
        state.displacement_m[:] = shift
        fields = project_surface(state, camera)
        fields.update(optical_surface(state, camera, fields))
        expected = image_coordinates(reference + shift, camera)
        rgb = Renderer(config).render(
            fields, expected, image_coordinates(reference, camera)
        )
        weight = (rgb[0, 0].astype(float) - rgb).sum(axis=2)
        rr, cc = np.indices(weight.shape)
        center = np.array([(cc * weight).sum(), (rr * weight).sum()]) / weight.sum()
        np.testing.assert_allclose(center, expected[0], atol=0.03)

    @unittest.skipUnless(
        os.environ.get("GELSIGHT_TEST_CUDA") == "1", "Opt-in CUDA test"
    )
    def test_cuda_material_texture_matches_cpu(self):
        cpu = self.render_stretch("cpu", 1.5)
        cuda = self.render_stretch("cuda", 1.5)
        self.assertLessEqual(np.abs(cpu.astype(int) - cuda.astype(int)).max(), 1)
