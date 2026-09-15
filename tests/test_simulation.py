"""Physics/export contracts that run without an ANSYS installation or license."""

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gelsight_ansys.ansys.session import gpu_statistics
from gelsight_ansys.config import Camera, Config, Gel, Material, Pose
from gelsight_ansys.contracts import SurfaceState
from gelsight_ansys.mesh import nodal_areas_normals, structured_mesh
from gelsight_ansys.metrics import frame_metrics, validate_frame
from gelsight_ansys.optics import Renderer
from gelsight_ansys.surface import Markers, image_coordinates, project_surface


def flat_state():
    mesh = structured_mesh(
        Gel(width_m=2, length_m=2, thickness_m=1, elements=(2, 2, 2))
    )
    xyz = mesh.coordinates[mesh.surface_nodes]
    q = len(mesh.surface_quads)
    areas, _ = nodal_areas_normals(xyz, mesh.triangles)
    traction = np.array([2.0, -3.0, -5.0])
    forces = areas[:, None] * traction
    total = forces.sum(axis=0)
    return SurfaceState(
        0.0,
        xyz,
        np.zeros_like(xyz),
        mesh.triangles,
        mesh.surface_quads,
        forces,
        np.full(q, 5.0),
        np.full(q, 3.0),
        np.zeros(q),
        -total,
        total,
        np.zeros(3),
        np.zeros(3),
        mesh.surface_nodes + 1,
    )


def full_camera(**kwargs):
    return Camera(
        width_px=17, height_px=13, fov_width_m=2.2, fov_height_m=2.2, **kwargs
    )


class ConfigurationTests(unittest.TestCase):
    def test_examples_round_trip(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "configs").glob("*.json"):
            config = Config.load(path)
            gel = config.gel
            mesh = structured_mesh(config.gel)
            if gel.tapered:
                # A gel that narrows has no single cross-section. Each layer is
                # still uniform, and the two that matter are the face the object
                # touches and the backing the gel is bonded to.
                for nodes, extents in (
                    (mesh.surface_nodes, (gel.top_width_m, gel.top_length_m)),
                    (mesh.bottom_nodes, (gel.width_m, gel.length_m)),
                ):
                    layer = mesh.coordinates[nodes]
                    for axis, extent in enumerate(extents):
                        np.testing.assert_allclose(
                            np.diff(np.unique(layer[:, axis])),
                            extent / gel.elements[axis],
                        )
                np.testing.assert_allclose(
                    np.diff(np.unique(mesh.coordinates[:, 2])),
                    gel.thickness_m / gel.elements[2],
                )
            else:
                for axis, width, count in zip(
                    range(3),
                    (gel.width_m, gel.length_m, gel.thickness_m),
                    gel.elements,
                ):
                    np.testing.assert_allclose(
                        np.diff(np.unique(mesh.coordinates[:, axis])), width / count
                    )
            self.assertEqual(
                config.to_dict(), Config.from_dict(config.to_dict()).to_dict()
            )

    def test_rejects_invalid_physical_configuration(self):
        base = Config()
        bad = [
            replace(base, material=replace(base.material, poisson=0.5)),
            replace(base, gel=replace(base.gel, elements=(1, 2, 2))),
            replace(base, camera=replace(base.camera, center_x_m=float("nan"))),
            replace(base, trajectory=(Pose(0, 0), Pose(1, 0.001))),
            replace(base, solver=replace(base.solver, gpu=False, require_gpu=True)),
            replace(base, trajectory=(base.trajectory[0], Pose(0, 0.001))),
            replace(base, name="../outside"),
            replace(
                base,
                visualization=replace(base.visualization, save_tactile_gif="false"),
            ),
        ]
        for config in bad:
            with self.subTest(config=config), self.assertRaises(ValueError):
                config.validate()

    def test_gpu_evidence_requires_accelerated_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gel.DSP"
            path.write_text(
                "GPU acceleration activated\npercentage of GPU accelerated flops = 0.0000\n"
            )
            self.assertFalse(gpu_statistics(directory)["active"])
            path.write_text(
                "GPU acceleration activated\npercentage of GPU accelerated flops = 100.0000\n"
            )
            self.assertTrue(gpu_statistics(directory)["active"])

    def test_nonconverged_solution_is_rejected_even_at_requested_time(self):
        from gelsight_ansys.ansys.session import validate_solve

        validate_solve(1, "solution converged", 0.4, 0.4, 2, 2)
        with self.assertRaisesRegex(RuntimeError, "nonconverged"):
            validate_solve(0, "", 0.4, 0.4, 2, 2)
        with self.assertRaisesRegex(RuntimeError, "nonconverged"):
            validate_solve(
                1,
                "Solution not converged. Run continued at user request.",
                0.4,
                0.4,
                2,
                2,
            )

    def test_recovered_bisection_requires_final_convergence_and_history(self):
        from gelsight_ansys.ansys.session import validate_solve

        recovered = (
            "Element has excessive distortion. BEGIN BISECTION. Solution converged."
        )
        validate_solve(1, recovered, 0.4, 0.4, 2, 2)
        for flag, text, actual, step in (
            (0, recovered, 0.4, 2),
            (1, "Run terminated.", 0.4, 2),
            (1, recovered, 0.3, 2),
            (1, recovered, 0.4, 1),
        ):
            with self.assertRaises(RuntimeError):
                validate_solve(flag, text, actual, 0.4, step, 2)

    def test_material_constants_recover_young_and_poisson(self):
        material = Material()
        mu, bulk = material.shear_pa, material.bulk_pa
        self.assertAlmostEqual(9 * bulk * mu / (3 * bulk + mu), material.young_pa)
        self.assertAlmostEqual(
            (3 * bulk - 2 * mu) / (2 * (3 * bulk + mu)), material.poisson
        )


class SurfaceTests(unittest.TestCase):
    def test_mesh_volume_backing_and_surface_orientation(self):
        gel = Gel(elements=(4, 3, 2))
        mesh = structured_mesh(gel)
        self.assertEqual(mesh.hexes.shape, (24, 8))
        cell = mesh.coordinates[mesh.hexes[0]]
        determinant = np.linalg.det(
            np.stack((cell[1] - cell[0], cell[3] - cell[0], cell[4] - cell[0]))
        )
        self.assertAlmostEqual(
            determinant * 24, gel.width_m * gel.length_m * gel.thickness_m
        )
        np.testing.assert_allclose(
            mesh.coordinates[mesh.bottom_nodes, 2], -gel.thickness_m
        )
        area, normals = nodal_areas_normals(
            mesh.coordinates[mesh.surface_nodes], mesh.triangles
        )
        self.assertAlmostEqual(area.sum(), gel.width_m * gel.length_m)
        np.testing.assert_allclose(normals, np.tile([0, 0, 1], (len(normals), 1)))

    def test_contact_refinement_preserves_volume_and_covers_motion_region(self):
        base = Config()
        uniform = structured_mesh(base.gel)
        for axis in range(3):
            sizes = np.diff(np.unique(uniform.coordinates[:, axis]))
            np.testing.assert_allclose(sizes, sizes[0])
        config = base.with_contact_refinement()
        self.assertIsNone(base.gel.contact_element_size_m)
        gel = config.gel
        mesh = structured_mesh(gel)
        self.assertEqual(len(mesh.hexes), np.prod(gel.elements))
        for axis, extent in enumerate(gel.refinement_half_extents_m):
            values = np.unique(mesh.coordinates[:, axis])
            np.testing.assert_allclose(values, -values[::-1], atol=1e-17)
            sizes = np.diff(values)
            centers = (values[:-1] + values[1:]) / 2
            np.testing.assert_allclose(sizes[abs(centers) < extent], 0.0003, atol=1e-17)
            ratios = sizes[1:] / sizes[:-1]
            self.assertLess(max(ratios.max(), (1 / ratios).max()), 1.7)
        xyz = mesh.coordinates[mesh.hexes]
        volumes = np.einsum(
            "ij,ij->i",
            xyz[:, 1] - xyz[:, 0],
            np.cross(xyz[:, 3] - xyz[:, 0], xyz[:, 4] - xyz[:, 0]),
        )
        self.assertGreater(volumes.min(), 0)
        self.assertAlmostEqual(
            volumes.sum(), gel.width_m * gel.length_m * gel.thickness_m
        )
        z = np.unique(mesh.coordinates[:, 2])
        self.assertLess(z[-1] - z[-2], 0.0002)

    def test_sphere_boundary_radius_orientation_and_positive_jacobians(self):
        from gelsight_ansys.mesh import sphere_mesh

        ind = Config().indenter
        center = np.array([0.0, 0.0, ind.radius_m + ind.clearance_m])
        mesh = sphere_mesh(ind, center)
        boundary = np.unique(mesh.surface_quads)
        np.testing.assert_allclose(
            np.linalg.norm(mesh.coordinates[boundary] - center, axis=1),
            ind.radius_m,
            atol=1e-15,
        )
        faces = mesh.coordinates[mesh.surface_quads]
        normal = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
        self.assertTrue(
            np.all(np.sum(normal * (faces.mean(axis=1) - center), axis=1) > 0)
        )
        # Check all eight Gauss points, not only the center of a warped hex.
        signs = np.array(
            [
                [-1, -1, -1],
                [1, -1, -1],
                [1, 1, -1],
                [-1, 1, -1],
                [-1, -1, 1],
                [1, -1, 1],
                [1, 1, 1],
                [-1, 1, 1],
            ]
        )
        xyz = mesh.coordinates[mesh.hexes]
        for point in signs / np.sqrt(3):
            deriv = np.empty((8, 3))
            for axis in range(3):
                others = [i for i in range(3) if i != axis]
                deriv[:, axis] = (
                    signs[:, axis]
                    * np.prod(1 + signs[:, others] * point[others], axis=1)
                    / 8
                )
            jacobian = np.einsum("eia,ib->eab", xyz, deriv)
            self.assertGreater(np.linalg.det(jacobian).min(), 0)
        self.assertLess(
            mesh.coordinates[mesh.grip_nodes, 2].min(), center[2] + ind.radius_m
        )
        self.assertGreaterEqual(
            mesh.coordinates[mesh.grip_nodes, 2].min(),
            center[2] + ind.grip_height_fraction * ind.radius_m,
        )

    def test_full_fov_preserves_arbitrary_vector_loads(self):
        state = flat_state()
        rng = np.random.default_rng(12)
        state.contact_force_n = rng.normal(size=state.contact_force_n.shape)
        fields = project_surface(state, full_camera())
        expected = state.contact_force_n.sum(axis=0)
        np.testing.assert_allclose(
            fields["pixel_force_n"].sum(axis=(0, 1)), expected, atol=2e-13
        )
        np.testing.assert_allclose(fields["fov_force_n"], expected, atol=2e-13)
        np.testing.assert_allclose(fields["raster_force_error_n"], 0, atol=2e-13)

    def test_partial_fov_integrates_clipped_surface_without_rescaling(self):
        state = flat_state()
        camera = Camera(
            width_px=19,
            height_px=11,
            fov_width_m=1.0,
            fov_height_m=1.4,
            center_x_m=0.9,
            center_y_m=0.1,
        )
        fields = project_surface(state, camera)
        # Intersection x=[.4,1], y=[-.6,.8]; a constant traction has an exact integral.
        expected = np.array([2.0, -3.0, -5.0]) * 0.6 * 1.4
        np.testing.assert_allclose(fields["fov_force_n"], expected, atol=2e-13)
        np.testing.assert_allclose(
            fields["pixel_force_n"].sum(axis=(0, 1)), expected, atol=2e-13
        )
        self.assertFalse(np.allclose(expected, state.contact_force_n.sum(axis=0)))

    def test_affine_displacement_normals_and_markers(self):
        state = flat_state()
        markers = Markers(state, 0.4)
        state.displacement_m[:, 0] = 0.1
        state.displacement_m[:, 1] = -0.05
        state.displacement_m[:, 2] = (
            0.2 * state.reference_m[:, 0] - 0.1 * state.reference_m[:, 1] - 0.3
        )
        state.contact_force_n[:] = 0
        state.contact_status[:] = 0
        moved = markers.positions(state)
        expected = markers.reference_m.copy()
        expected[:, 0] += 0.1
        expected[:, 1] -= 0.05
        expected[:, 2] += (
            0.2 * markers.reference_m[:, 0] - 0.1 * markers.reference_m[:, 1] - 0.3
        )
        np.testing.assert_allclose(moved, expected, atol=1e-15)
        fields = project_surface(state, full_camera())
        normal = np.array([-0.2, 0.1, 1.0])
        normal /= np.linalg.norm(normal)
        valid = fields["valid_mask"]
        np.testing.assert_allclose(
            fields["normals"][valid], np.tile(normal, (valid.sum(), 1)), atol=1e-14
        )
        self.assertTrue((fields["normal_displacement_m"][valid] > 0).all())

    def test_camera_axes_and_fold_rejection(self):
        camera = full_camera()
        pixels = image_coordinates(np.array([[0.0, 0.0, 0.0], [0.1, 0.1, 0.0]]), camera)
        self.assertGreater(pixels[1, 0], pixels[0, 0])
        self.assertLess(pixels[1, 1], pixels[0, 1])
        state = flat_state()
        state.displacement_m[:, 0] = -2 * state.reference_m[:, 0]
        with self.assertRaisesRegex(ValueError, "folds"):
            project_surface(state, camera)

    def test_serialization_and_equilibrium_reject_wrong_force_sign(self):
        state = flat_state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.npz"
            state.save(path)
            restored = SurfaceState.load(path)
            for name, value in state.__dict__.items():
                np.testing.assert_equal(getattr(restored, name), value)
        fields = project_surface(state, full_camera())
        markers = Markers(state, 0.4)
        metrics = frame_metrics(
            state,
            fields,
            markers.reference_m,
            markers.positions(state),
            Pose(0, 0),
            {"active": False},
        )
        validate_frame(metrics, Config())
        self.assertAlmostEqual(metrics["normal_force_n"], 20.0)
        state.backing_reaction_n *= -1
        metrics = frame_metrics(
            state,
            fields,
            markers.reference_m,
            markers.positions(state),
            Pose(0, 0),
            {"active": False},
        )
        with self.assertRaisesRegex(RuntimeError, "backing"):
            validate_frame(metrics, Config())


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.state = flat_state()
        base = Config()
        self.config = replace(
            base,
            camera=full_camera(),
            optics=replace(
                base.optics, model="analytic", backend="cpu", marker_radius_m=0.05
            ),
        )
        self.fields = project_surface(self.state, self.config.camera)
        self.markers = np.array([[4.2, 5.1], [10.4, 8.8]])

    def test_flat_surface_preserves_supplied_background(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            pixels = (
                np.arange(13 * 17 * 3, dtype=np.uint16)
                .reshape(13, 17, 3)
                .astype(np.uint8)
            )
            Image.fromarray(pixels).save(Path(directory) / "background.png")
            config = replace(
                self.config,
                optics=replace(self.config.optics, background_image="background.png"),
            )
            image = Renderer(config, directory).render(self.fields, np.empty((0, 2)))
            np.testing.assert_array_equal(image, pixels)

    def test_markers_darken_and_normals_change_rgb(self):
        renderer = Renderer(self.config)
        plain = renderer.render(self.fields, np.empty((0, 2)))
        marked = renderer.render(self.fields, self.markers)
        self.assertTrue((marked <= plain).all())
        self.assertLess(int(marked.sum()), int(plain.sum()))
        self.fields["normals"][..., 0] = 0.3
        self.fields["normals"][..., 2] = np.sqrt(1 - 0.3**2)
        tilted = renderer.render(self.fields, np.empty((0, 2)))
        self.assertGreater(np.abs(tilted.astype(float) - plain).max(), 5)
        np.testing.assert_array_equal(
            tilted[~self.fields["valid_mask"]], plain[~self.fields["valid_mask"]]
        )

    @unittest.skipUnless(
        os.environ.get("GELSIGHT_TEST_CUDA") == "1",
        "Opt-in GPU check; CPU CI does not require CUDA",
    )
    def test_cuda_matches_cpu_with_tilt_and_markers(self):
        self.fields["normals"][..., 0] = 0.3
        self.fields["normals"][..., 2] = np.sqrt(1 - 0.3**2)
        cpu = Renderer(self.config).render(self.fields, self.markers)
        gpu_config = replace(
            self.config, optics=replace(self.config.optics, backend="cuda")
        )
        cuda = Renderer(gpu_config).render(self.fields, self.markers)
        self.assertLessEqual(np.abs(cpu.astype(int) - cuda.astype(int)).max(), 1)


class ProcessReportTests(unittest.TestCase):
    def test_cycle_gif_and_marker_scale_preserve_physical_data(self):
        """A diagnostic animation must retain release and not magnify dataset arrays."""
        import json
        from copy import deepcopy

        from PIL import Image

        from gelsight_ansys.artifacts import build_report
        from gelsight_ansys.run_services import process_frame

        base = Config()
        config = replace(
            base,
            name="report_test",
            # Exercise default storage first; opt-in diagnostics are tested below.
            visualization=replace(base.visualization, save_panel_frames=False),
            gel=Gel(width_m=2, length_m=2, thickness_m=1, elements=(2, 2, 2)),
            camera=full_camera(),
            optics=replace(
                base.optics,
                backend="cpu",
                marker_spacing_m=0.4,
                marker_radius_m=0.05,
                animation_fps=5,
            ),
            trajectory=(Pose(0, -0.0001), Pose(1, 0.02), Pose(2, -0.0001)),
        )
        unloaded = flat_state()
        unloaded.source = "synthetic_test"
        unloaded.contact_force_n[:] = 0
        unloaded.contact_pressure_pa[:] = 0
        unloaded.contact_status[:] = 0
        unloaded.backing_reaction_n[:] = 0
        unloaded.pilot_reaction_n[:] = 0
        loaded = flat_state()
        loaded.source = "synthetic_test"
        loaded.time_s = 1.0
        loaded.displacement_m[:, 0] = 0.02
        loaded.displacement_m[:, 2] = -0.02
        released = deepcopy(unloaded)
        released.time_s = 2.0
        markers = Markers(unloaded, config.optics.marker_spacing_m)
        renderer = Renderer(config)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            metrics = []
            for index, state in enumerate((unloaded, loaded, released)):
                metrics.append(
                    process_frame(
                        directory,
                        index,
                        config,
                        state,
                        markers,
                        renderer,
                        config.trajectory[index],
                        {"active": False, "sparse_statistics": []},
                    )
                )
            original = {
                p.relative_to(directory): p.read_bytes()
                for p in directory.rglob("*")
                if p.is_file()
            }
            build_report(directory, config, metrics)
            for relative, content in original.items():
                self.assertEqual(content, (directory / relative).read_bytes())
            self.assertFalse((directory / "preview.gif").exists())
            self.assertFalse((directory / "tactile.gif").exists())
            self.assertFalse((directory / "panels").exists())
            page = (directory / "report.html").read_text()
            self.assertIn('const frameFolder="images"', page)
            self.assertIn('src="process.gif"', page)
            self.assertNotIn('href="tactile.gif"', page)
            scales = json.loads((directory / "visualization.json").read_text())
            self.assertEqual(scales["marker_scale"], 10.0)
            self.assertEqual(scales["frame_viewer_folder"], "images")
            self.assertFalse(scales["tactile_gif_saved"])
            self.assertAlmostEqual(
                scales["color_limits"]["marker_in_plane_um"][1], 20000.0
            )
            self.assertEqual(scales["animation_durations_ms"], [700, 200, 700])
            with Image.open(directory / "process.gif") as gif:
                self.assertEqual(gif.n_frames, 3)
                self.assertEqual(gif.info["loop"], 0)
                durations = []
                for index in range(gif.n_frames):
                    gif.seek(index)
                    durations.append(gif.info["duration"])
                self.assertEqual(durations, [700, 200, 700])
            with (
                Image.open(directory / "images/frame_0000.png") as first,
                Image.open(directory / "images/frame_0002.png") as last,
            ):
                np.testing.assert_array_equal(np.asarray(first), np.asarray(last))
            compact_bytes = sum(
                p.stat().st_size for p in directory.rglob("*") if p.is_file()
            )
            detailed = replace(
                config,
                visualization=replace(
                    config.visualization, save_panel_frames=True, save_tactile_gif=True
                ),
            )
            # Regenerate omitted diagnostics from the same saved data, without ANSYS.
            build_report(directory, detailed, metrics)
            for relative, content in original.items():
                self.assertEqual(content, (directory / relative).read_bytes())
            for index in range(3):
                self.assertTrue(
                    (directory / "panels" / f"frame_{index:04d}.png").is_file()
                )
            self.assertTrue((directory / "tactile.gif").is_file())
            self.assertFalse((directory / "preview.gif").exists())
            page = (directory / "report.html").read_text()
            self.assertIn('const frameFolder="panels"', page)
            self.assertIn('href="tactile.gif"', page)
            scales = json.loads((directory / "visualization.json").read_text())
            self.assertEqual(scales["frame_viewer_folder"], "panels")
            self.assertTrue(scales["tactile_gif_saved"])
            self.assertEqual(
                (directory / "preview.png").read_bytes(),
                (
                    directory / "panels" / f"frame_{scales['preview_frame']:04d}.png"
                ).read_bytes(),
            )
            detailed_bytes = sum(
                p.stat().st_size for p in directory.rglob("*") if p.is_file()
            )
            self.assertLess(compact_bytes, detailed_bytes)


if __name__ == "__main__":
    unittest.main()
