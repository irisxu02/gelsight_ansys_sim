"""Independent object simplification must preserve geometry and the gel grid."""

import unittest
from pathlib import Path

import numpy as np

from gelsight_ansys.config import Config, Gel
from gelsight_ansys.mesh import structured_mesh
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_mesh import gel_mesh, slab_mesh, textured_target
from gelsight_ansys.simulation_config import config_for_plane
from sampling_grid import expected_count

ROOT = Path(__file__).resolve().parents[1]


class PlaneMeshTests(unittest.TestCase):
    def case(self, name):
        return PlaneCase.load(ROOT / f"configs/material_plane_slide/{name}.json")

    def test_default_gel_matches_standard_sensor_and_refinement_is_explicit(self):
        case = self.case("rigid_reference")
        mesh = gel_mesh(case)
        standard = structured_mesh(Gel(**case.suite["sensor"]["gel"]))
        self.assertEqual(len(mesh.hexes), 8640)
        np.testing.assert_array_equal(mesh.coordinates, standard.coordinates)
        np.testing.assert_array_equal(mesh.hexes, standard.hexes)
        case.suite["discretization"]["gel_mesh"] = "refined"
        refined = gel_mesh(case)
        self.assertEqual(len(refined.hexes), 402384)
        np.testing.assert_allclose(
            np.ptp(mesh.coordinates, axis=0), np.ptp(refined.coordinates, axis=0)
        )
        case.suite["discretization"]["gel_mesh"] = "unknown"
        with self.assertRaisesRegex(ValueError, "Gel mesh"):
            case.validate()

    def test_saving_fewer_frames_preserves_mechanical_checkpoints_and_load_corners(self):
        case = self.case("soft_rubber")
        reference = case.solve_times.copy()
        original = config_for_plane(case)
        sampled = original.with_plane_sampling(sample_interval_s=0.05)
        frames = expected_count(case, 0.05, "sample_interval_s")
        self.assertEqual(len(sampled.trajectory), frames)
        np.testing.assert_array_equal(sampled.specification.solve_times, reference)
        self.assertEqual(
            len(original.trajectory), expected_count(case, 0.01, "sample_interval_s")
        )
        self.assertEqual(sampled.solver, original.solver)
        self.assertEqual(sampled.material, original.material)
        self.assertEqual(
            sampled.specification.suite["protocol"],
            original.specification.suite["protocol"],
        )
        case.suite["dataset"].update(solve_interval_s=0.01, sample_interval_s=0.05)
        case.validate()
        self.assertEqual(len(case.frame_times), frames)
        np.testing.assert_array_equal(case.solve_times, reference)
        config = config_for_plane(case)
        self.assertEqual(len(config.trajectory), frames)
        np.testing.assert_array_equal(
            Config.from_dict(config.to_dict()).specification.solve_times, reference
        )
        case.suite["dataset"].update(solve_interval_s=0.02, sample_interval_s=0.02)
        case.validate()
        self.assertEqual(
            len(case.solve_times), expected_count(case, 0.02, "solve_interval_s")
        )
        self.assertTrue(
            all(
                p["time_s"] in case.solve_times
                for p in case.suite["protocol"]["keyframes"]
            )
        )
        # Divides the recording, but is not a whole number of solve intervals.
        case.suite["dataset"]["sample_interval_s"] = 0.155
        with self.assertRaisesRegex(ValueError, "integer multiple"):
            case.validate()

    def test_rigid_simplification_is_the_same_exact_plane(self):
        case = self.case("rigid_reference")
        small, faces = textured_target(case, 0)
        fine, _ = textured_target(case, 0, object_mode="matched")
        self.assertEqual(small.shape, (4, 3))
        self.assertEqual(faces.shape, (1, 4))
        np.testing.assert_allclose(small.min(axis=0), fine.min(axis=0))
        np.testing.assert_allclose(small.max(axis=0), fine.max(axis=0))
        normal = np.cross(small[1] - small[0], small[2] - small[0])
        self.assertLess(normal[2], 0)
        self.assertAlmostEqual(abs(normal[2]), 0.06 * 0.035)

    def test_deformable_grid_reduction_preserves_faces_and_thickness(self):
        case = self.case("soft_rubber")
        mesh = slab_mesh(case, 0)
        self.assertEqual(len(mesh.hexes), 33600)
        self.assertEqual(len(mesh.coordinates), 42955)
        np.testing.assert_allclose(np.ptp(mesh.coordinates, axis=0), [0.06, 0.035, 0.003])
        np.testing.assert_allclose(mesh.coordinates[np.unique(mesh.surface_quads), 2], 0)
        np.testing.assert_allclose(mesh.coordinates[mesh.grip_nodes, 2], 0.003)
        # Object settings cannot affect the separate gel mesh builder.
        a = gel_mesh(case, 0.001)
        config = config_for_plane(
            case, element_size_m=0.001, object_element_size_m=0.00075
        )
        b = gel_mesh(config.specification, config.plane_options.element_size_m)
        np.testing.assert_array_equal(a.coordinates, b.coordinates)
        np.testing.assert_array_equal(a.hexes, b.hexes)
        self.assertEqual(Config.from_dict(config.to_dict()).to_dict(), config.to_dict())

    def test_rough_geometry_is_not_flattened_by_simplification(self):
        """Simplifying the target coarsens its grid; it keeps the height field."""
        case = self.case("rough_surface")
        a, qa = textured_target(case, 0)
        b, qb = textured_target(case, 0, object_mode="matched")
        peak = sum(m["amplitude_m"] for m in case.case["surface_geometry"]["modes"])
        for points in (a, b):
            self.assertAlmostEqual(np.ptp(points[:, 2]), 2 * peak, places=7)
        self.assertLess(len(qa), len(qb))
        # The matched grid is the contact-matched one it exists to compare with.
        self.assertEqual(
            len(qb),
            int(np.ceil(0.06 / 0.000125)) * int(np.ceil(0.035 / 0.000125)),
        )

    def test_a_simplified_texture_still_resolves_its_shortest_wavelength(self):
        from gelsight_ansys.plane_mesh import texture_edge

        case = self.case("rough_surface")
        declared = case.case["surface_geometry"]["minimum_elements_per_shortest_wavelength"]
        edge = texture_edge(case)
        self.assertGreaterEqual(case.shortest_wavelength() / edge, declared)
        # And it is no coarser than the gel faces that have to sense it.
        gel = case.suite["sensor"]["gel"]
        self.assertLessEqual(edge, gel["width_m"] / gel["elements"][0])
        # Its deviation from the exact height field stays well under a micron.
        points, _ = textured_target(case, 0)
        x = np.unique(points[:, 0])
        fine = np.linspace(x[0], x[1], 33)
        exact = case.surface_height(fine, np.zeros_like(fine))
        interpolated = np.interp(fine, x[:2], case.surface_height(x[:2], np.zeros(2)))
        self.assertLess(np.abs(exact - interpolated).max(), 1e-6)


if __name__ == "__main__":
    unittest.main()


class SharedTopologyTests(unittest.TestCase):
    """The gel and the slab are one tensor-grid topology with different axes."""

    def test_the_gel_grid_is_the_tensor_grid_of_its_axes(self):
        from gelsight_ansys.mesh import tensor_mesh

        gel = Gel(elements=(6, 5, 3), through_thickness_bias=1.5)
        mesh = structured_mesh(gel)
        x = np.unique(mesh.coordinates[:, 0])
        y = np.unique(mesh.coordinates[:, 1])
        z = np.unique(mesh.coordinates[:, 2])
        same = tensor_mesh(x, y, z)
        np.testing.assert_array_equal(mesh.coordinates, same.coordinates)
        np.testing.assert_array_equal(mesh.hexes, same.hexes)
        np.testing.assert_array_equal(mesh.surface_quads, same.surface_quads)
        np.testing.assert_array_equal(mesh.surface_nodes, same.surface_nodes)
        np.testing.assert_array_equal(mesh.bottom_nodes, same.bottom_nodes)
        # x runs fastest, the surface is the last z layer, the bottom the first.
        self.assertEqual(list(mesh.hexes[0]), [0, 1, 8, 7, 42, 43, 50, 49])
        self.assertTrue(np.all(mesh.coordinates[mesh.surface_nodes, 2] == 0))
        self.assertTrue(np.all(mesh.coordinates[mesh.bottom_nodes, 2] == z[0]))

    def test_a_textured_surface_is_refused_on_a_deformable_slab(self):
        """The slab is meshed flat, so accepting the texture would solve a smooth one."""
        import copy

        soft = Config.load(ROOT / "configs/material_plane_slide/soft_rubber.json")
        rough = Config.load(ROOT / "configs/material_plane_slide/rough_surface.json")
        textured = copy.deepcopy(soft)
        textured.specification.case["surface_geometry"] = rough.specification.case[
            "surface_geometry"
        ]
        with self.assertRaisesRegex(ValueError, "textured deformable slab"):
            textured.validate()
        rough.validate()
