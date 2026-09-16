"""A gel that narrows towards the face it senses with."""

import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from gelsight_ansys.camera import pixels_to_reference, reference_marker_pixels
from gelsight_ansys.config import Config, Gel
from gelsight_ansys.mesh import structured_mesh
from gelsight_ansys.plane_config import PlaneCase

ROOT = Path(__file__).resolve().parents[1]
PRESS = ROOT / "configs/custom_gel_press.json"
CYLINDER = ROOT / "configs/custom_gel_cylinder_slide/cylinder_20mm.json"
# The pad the user asked for: 3 mm of straight wall under 2 mm of taper.
PAD = Gel(
    thickness_m=0.005,
    elements=(36, 30, 10),
    top_width_m=0.022,
    top_length_m=0.016,
    taper_height_m=0.002,
)


class TaperedPadTests(unittest.TestCase):
    def test_the_pad_is_the_shape_it_declares(self):
        mesh = structured_mesh(PAD)
        top = mesh.coordinates[mesh.surface_nodes]
        bottom = mesh.coordinates[mesh.bottom_nodes]
        np.testing.assert_allclose(np.ptp(top[:, :2], axis=0), [0.022, 0.016])
        np.testing.assert_allclose(np.ptp(bottom[:, :2], axis=0), [0.02525, 0.02075])
        np.testing.assert_allclose(top[:, 2], 0)
        np.testing.assert_allclose(bottom[:, 2], -0.005)
        # The straight wall keeps the backing's section all the way to the taper.
        for height in (-0.005, -0.004, -0.003, -0.002):
            layer = mesh.coordinates[np.isclose(mesh.coordinates[:, 2], height)]
            self.assertTrue(len(layer))
            np.testing.assert_allclose(
                np.ptp(layer[:, :2], axis=0), [0.02525, 0.02075]
            )
        # And the transition is linear between the two sections.
        for height, fraction in ((-0.0015, 0.25), (-0.001, 0.5), (-0.0005, 0.75)):
            layer = mesh.coordinates[np.isclose(mesh.coordinates[:, 2], height)]
            expected = [
                0.02525 + fraction * (0.022 - 0.02525),
                0.02075 + fraction * (0.016 - 0.02075),
            ]
            np.testing.assert_allclose(np.ptp(layer[:, :2], axis=0), expected)

    def test_a_node_layer_lands_where_the_taper_begins(self):
        """Otherwise the elements crossing it build a chamfer, not the shape."""
        for count, bias in ((6, 0.0), (8, 0.0), (10, 0.0), (16, 0.0), (8, 2.0)):
            with self.subTest(layers=count, bias=bias):
                mesh = structured_mesh(
                    replace(PAD, elements=(6, 6, count), through_thickness_bias=bias)
                )
                z = np.unique(mesh.coordinates[:, 2])
                self.assertIn(-0.002, z)
                # The boundary is joined to the axis the gel's own rules made,
                # never doubled by a second level a rounding error away.
                self.assertIn(len(z) - 1, (count, count + 1))
                self.assertGreater(np.diff(z).min(), 1e-9)
                # A layer count that suits the taper needs no extra level.
                if count == 10:
                    self.assertEqual(len(z) - 1, 10)

    def test_narrowing_moves_coordinates_and_nothing_else(self):
        """The grid is the prism's, so everything built on it is unchanged."""
        prism = replace(PAD, top_width_m=None, top_length_m=None, taper_height_m=None)
        a, b = structured_mesh(prism), structured_mesh(PAD)
        for name in ("hexes", "surface_quads", "surface_nodes", "bottom_nodes", "triangles"):
            np.testing.assert_array_equal(getattr(a, name), getattr(b, name))
        self.assertEqual(len(a.coordinates), len(b.coordinates))
        # Only the section moves; the depth of every node is the same.
        np.testing.assert_allclose(a.coordinates[:, 2], b.coordinates[:, 2])
        self.assertFalse(np.allclose(a.coordinates[:, 0], b.coordinates[:, 0]))

    def test_an_incomplete_or_impossible_pad_is_refused(self):
        base = Config.load(PRESS)
        for overrides, message in (
            ({"taper_height_m": None}, "needs top_width_m"),
            ({"top_width_m": 0.03}, "cannot exceed its backing"),
            ({"top_length_m": 0.03}, "cannot exceed its backing"),
            ({"taper_height_m": 0.005}, "shorter than the gel"),
            ({"taper_height_m": -0.001}, "must be finite and positive"),
        ):
            with self.subTest(**overrides):
                with self.assertRaisesRegex(ValueError, message):
                    replace(base, gel=replace(base.gel, **overrides)).validate()

    def test_every_mesh_rule_still_builds_the_declared_pad(self):
        """The taper is a coordinate map, so it composes with any depth axis."""
        from gelsight_ansys.plane_mesh import gel_mesh

        case = PlaneCase.load(CYLINDER)
        refined = PlaneCase(case.suite, case.case)
        refined.suite["discretization"]["gel_mesh"] = "refined"
        for label, built in (
            ("uniform", gel_mesh(case, None)),
            ("explicit element size", gel_mesh(case, 0.0005)),
            ("refined", gel_mesh(refined, None)),
        ):
            with self.subTest(label):
                top = built.coordinates[built.surface_nodes]
                bottom = built.coordinates[built.bottom_nodes]
                np.testing.assert_allclose(np.ptp(top[:, :2], axis=0), [0.022, 0.016])
                np.testing.assert_allclose(
                    np.ptp(bottom[:, :2], axis=0), [0.02525, 0.02075]
                )
                self.assertIn(-0.002, np.unique(built.coordinates[:, 2]))
        # And the indenter pipeline's contact refinement now applies to one too.
        graded = Config.load(PRESS).with_contact_refinement()
        self.assertEqual(graded.gel.through_thickness_bias, 2.0)
        mesh = structured_mesh(graded.gel)
        self.assertIn(-0.002, np.unique(mesh.coordinates[:, 2]))
        np.testing.assert_allclose(
            np.ptp(mesh.coordinates[mesh.surface_nodes][:, :2], axis=0), [0.022, 0.016]
        )


class CustomMarkerArrayTests(unittest.TestCase):
    def markers(self, config):
        return pixels_to_reference(
            reference_marker_pixels(config.camera, config.optics), config.camera
        )

    def test_the_array_is_eleven_by_seventeen_round_dots(self):
        config = Config.load(PRESS)
        # A config read from JSON keeps its arrays as lists.
        self.assertEqual(tuple(config.optics.marker_grid_rows_cols), (11, 17))
        points = self.markers(config)
        self.assertEqual(len(points), 187)
        columns, rows = np.unique(points[:, 0]), np.unique(points[:, 1])
        self.assertEqual((len(rows), len(columns)), (11, 17))
        # A physical radius, so the renderer scales it separately in x and y and
        # draws a round 0.5 mm dot on a camera whose pixels are not square.
        self.assertIsNone(config.optics.marker_radius_px)
        self.assertAlmostEqual(config.optics.marker_radius_m * 2000, 0.5)
        for spacing in (np.diff(columns), np.diff(rows)):
            self.assertGreater(spacing.min(), 0.0005)

    def test_every_dot_is_inside_the_image_and_on_the_sensing_face(self):
        config = Config.load(PRESS)
        points = self.markers(config)
        radius = config.optics.marker_radius_m
        camera = config.camera
        self.assertLess(
            np.abs(points[:, 0]).max() + radius, camera.fov_width_m / 2
        )
        self.assertLess(
            np.abs(points[:, 1]).max() + radius, camera.fov_height_m / 2
        )
        # Attachment is by material coordinate, so a dot off the face is fatal.
        mesh = structured_mesh(config.gel)
        face = mesh.coordinates[mesh.surface_nodes]
        self.assertLess(np.abs(points[:, 0]).max(), face[:, 0].max())
        self.assertLess(np.abs(points[:, 1]).max(), face[:, 1].max())

    def test_the_markers_attach_to_the_narrowed_face(self):
        from gelsight_ansys.contracts import SurfaceState
        from gelsight_ansys.surface import Markers

        config = Config.load(PRESS)
        mesh = structured_mesh(config.gel)
        count = len(mesh.surface_nodes)
        faces = len(mesh.surface_quads)
        state = SurfaceState(
            0.0,
            mesh.coordinates[mesh.surface_nodes],
            np.zeros((count, 3)),
            mesh.triangles,
            mesh.surface_quads,
            np.zeros((count, 3)),
            np.zeros(faces),
            np.zeros(faces),
            np.zeros(faces),
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
            mesh.surface_nodes + 1,
        )
        markers = Markers(state, config.optics.marker_spacing_m, config.camera, config.optics)
        self.assertEqual(len(markers.reference_m), 187)
        np.testing.assert_allclose(
            markers.reference_m[:, :2], self.markers(config)[:, :2], atol=1e-12
        )


class ReachAgainstTheSensingFaceTests(unittest.TestCase):
    """A curved target has to reach the face it can touch, not the backing."""

    def test_a_narrower_face_needs_a_narrower_patch(self):
        nominal = PlaneCase.load(
            ROOT / "configs/cylinder_press_slide/cylinder_100mm.json"
        )
        tapered = PlaneCase(nominal.suite, nominal.case)
        tapered.suite["sensor"]["gel"] = Config.load(PRESS).to_dict()["gel"]
        self.assertEqual(tapered.sensing_face_m(), (0.022, 0.016))
        self.assertEqual(nominal.sensing_face_m(), (0.02525, 0.02075))
        # 11 mm of face plus the 2 mm margin, against 12.6 mm plus the same.
        self.assertLess(tapered.cylinder_wrap_rad(), nominal.cylinder_wrap_rad())
        self.assertAlmostEqual(tapered.target_half_extents()[0], 0.013)
        self.assertAlmostEqual(nominal.target_half_extents()[0], 0.014625)

    def test_the_shipped_tapered_cylinder_cases_are_the_nominal_ones_turned(self):
        """Same cylinders, same load, same stroke; the sensor is a quarter turn round."""
        for name in ("cylinder_20mm", "cylinder_100mm"):
            with self.subTest(name):
                config = Config.load(
                    ROOT / f"configs/custom_gel_cylinder_slide/{name}.json"
                )
                case = config.specification
                nominal = Config.load(
                    ROOT / f"configs/cylinder_press_slide/{name}.json"
                ).specification
                self.assertEqual(config.indenter.shape, "cylinder")
                self.assertTrue(config.gel.tapered)
                self.assertEqual(config.gel.thickness_m, 0.005)
                self.assertEqual(tuple(config.optics.marker_grid_rows_cols), (11, 17))
                # The cylinder itself is unchanged apart from which way it lies.
                self.assertEqual(case.cylinder["axis"], "x")
                self.assertEqual(nominal.cylinder["axis"], "y")
                for key in ("diameter_m", "length_m", "maximum_wrap_rad"):
                    self.assertEqual(case.cylinder[key], nominal.cylinder[key], key)
                # The protocol is the same one with its slide axis exchanged.
                turned = [
                    {**k, "x_m": k["y_m"], "y_m": k["x_m"]}
                    for k in case.suite["protocol"]["keyframes"]
                ]
                self.assertEqual(turned, nominal.suite["protocol"]["keyframes"])
                self.assertAlmostEqual(config.physical_pose(3.5).x_m, 0.004)
                self.assertEqual(config.physical_pose(3.5).y_m, 0.0)
                # Its contact line lies along the wide side of the narrowed face.
                self.assertEqual(case.sensing_face_m(), (0.022, 0.016))
                self.assertGreater(*case.target_half_extents())
                self.assertEqual(
                    config.to_dict(), Config.from_dict(config.to_dict()).to_dict()
                )


if __name__ == "__main__":
    unittest.main()
