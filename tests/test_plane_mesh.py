"""Independent object simplification must preserve geometry and the gel grid."""

import unittest
from pathlib import Path

import numpy as np

from gelsight_ansys.config import Config, Gel
from gelsight_ansys.mesh import structured_mesh
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_mesh import gel_mesh, slab_mesh, textured_target
from gelsight_ansys.simulation_config import config_for_plane

ROOT = Path(__file__).resolve().parents[1]


def expected_count(case, coarse_step, key):
    """Frames or checkpoints: the coarse grid joined with each window's own.

    The suite integrates the slide with mass, and a window keeps its own
    sampling and checkpoint grid whatever the dataset-level interval is, so
    coarsening the CLI interval thins only the quasi-static stretches.
    """
    start, end = case.suite["protocol"]["recorded_interval_s"]
    grids = [np.linspace(start, end, round((end - start) / coarse_step) + 1)]
    for w in case.transient_windows:
        step = w.get(key, w["time_increment_s"] if key == "solve_interval_s" else None)
        if step is not None:
            a, b = w["start_time_s"], w["end_time_s"]
            grids.append(np.linspace(a, b, round((b - a) / step) + 1))
    return len(np.unique(np.round(np.concatenate(grids), 12)))

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
        self.assertEqual(len(original.trajectory), 601)
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
        case.suite["dataset"]["sample_interval_s"] = 0.03
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
        case = self.case("rough_surface")
        a, qa = textured_target(case, 0)
        b, qb = textured_target(case, 0, object_mode="matched")
        np.testing.assert_array_equal(a, b)
        np.testing.assert_array_equal(qa, qb)
        self.assertGreater(np.ptp(a[:, 2]), 50e-6)
        self.assertEqual(len(qa), 134400)


if __name__ == "__main__":
    unittest.main()
