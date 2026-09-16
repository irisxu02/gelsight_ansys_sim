"""A curved rigid target: the patch cut from it, and what it is checked against."""

import math
import unittest
from pathlib import Path

import numpy as np

from gelsight_ansys.config import Config
from gelsight_ansys.fem_view import grid_shape, nice_ceiling
from gelsight_ansys.mesh import exterior_faces
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_coverage import ContactCoverage
from gelsight_ansys.plane_mesh import gel_mesh, textured_target

ROOT = Path(__file__).resolve().parents[1]
CASES = ("cylinder_100mm", "cylinder_20mm")


def load(name):
    return Config.load(ROOT / f"configs/cylinder_press_slide/{name}.json")


class CylinderTargetTests(unittest.TestCase):
    def test_the_patch_is_the_cylinder_it_declares(self):
        """Tangent at first touch, faceted within tolerance, facing the gel."""
        for name in CASES:
            with self.subTest(name):
                case = load(name).specification
                radius = case.cylinder["diameter_m"] / 2
                points, quads = textured_target(case, 0.0)
                # First touch is a node on the lowest line, not a chord above it.
                self.assertEqual(points[:, 2].min(), 0.0)
                self.assertIn(0.0, points[:, 0])
                # Every node lies on the cylinder's lateral surface.
                distance = np.hypot(points[:, 0], radius - points[:, 2])
                np.testing.assert_allclose(distance, radius, rtol=0, atol=1e-12)
                # The axis runs along y, so the surface does not vary along it.
                rows, columns = grid_shape(points)
                grid = points.reshape(rows, columns, 3)
                np.testing.assert_allclose(
                    grid[:, :, 2],
                    np.broadcast_to(grid[0, :, 2], grid.shape[:2]),
                    atol=0,
                )
                # A target's face normal points towards the gel.
                corners = points[quads[0]]
                normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
                self.assertLess(normal[2], 0)

    def test_facets_stay_within_the_declared_chord_deviation(self):
        for name in CASES:
            with self.subTest(name):
                case = load(name).specification
                radius = case.cylinder["diameter_m"] / 2
                declared = case.suite["discretization"]["object_mesh"][
                    "curved_target_chord_deviation_m"
                ]
                points, _ = textured_target(case, 0.0)
                angle = np.arcsin(np.clip(np.unique(points[:, 0]) / radius, -1, 1))
                sag = radius * (1 - np.cos(np.diff(angle).max() / 2))
                self.assertLessEqual(sag, declared)
                # And no coarser than the gel faces that have to sense it.
                gel = case.suite["sensor"]["gel"]
                self.assertLessEqual(
                    np.diff(np.unique(points[:, 0])).max(),
                    gel["width_m"] / gel["elements"][0],
                )

    def test_the_patch_covers_everything_the_cylinder_can_touch(self):
        """Either it reaches past the gel, or it has already turned away."""
        for name in CASES:
            with self.subTest(name):
                case = load(name).specification
                gel = case.suite["sensor"]["gel"]
                points, _ = textured_target(case, 0.0)
                across = points[:, 0].max()
                if across < gel["width_m"] / 2:
                    self.assertGreater(
                        points[:, 2].max(),
                        case.suite["contact_numerics"]["pinball_radius_m"],
                    )
                # Along its own axis it outlasts the gel plus the whole slide.
                travel = max(p["y_m"] for p in case.suite["protocol"]["keyframes"])
                self.assertGreater(
                    points[:, 1].max(), gel["length_m"] / 2 + travel + 0.001
                )

    def test_a_cylinder_shorter_than_its_slide_is_refused(self):
        case = load("cylinder_20mm").specification
        short = PlaneCase(case.suite, case.case)
        short.suite["specimen"]["length_m"] = 0.012
        with self.assertRaisesRegex(ValueError, "longer than the gel"):
            short.validate()

    def test_a_patch_that_stops_inside_the_pinball_is_refused(self):
        case = load("cylinder_20mm").specification
        truncated = PlaneCase(case.suite, case.case)
        truncated.suite["specimen"]["maximum_wrap_rad"] = 0.2
        with self.assertRaisesRegex(ValueError, "stops short of the gel"):
            truncated.validate()

    def test_a_deformable_cylinder_is_refused(self):
        case = load("cylinder_20mm").specification
        soft = PlaneCase(case.suite, case.case)
        soft.case["bulk_material"] = {
            "model": "neo_hookean",
            "young_pa": 1e6,
            "poisson": 0.45,
        }
        with self.assertRaisesRegex(ValueError, "rigid cylinder"):
            soft.validate()

    def test_the_resolved_run_is_a_cylinder_and_round_trips(self):
        for name in CASES:
            with self.subTest(name):
                config = load(name)
                self.assertEqual(config.indenter.shape, "cylinder")
                self.assertTrue(config.is_plane)
                diameter = config.specification.cylinder["diameter_m"]
                self.assertEqual(config.indenter.radius_m, diameter / 2)
                self.assertEqual(config.indenter.half_length_m, 0.025)
                self.assertEqual(
                    Config.from_dict(config.to_dict()).to_dict(), config.to_dict()
                )

    def test_the_protocol_holds_four_newtons_through_an_axial_slide(self):
        config = load("cylinder_20mm")
        case = config.specification
        press = config.physical_pose(2.0)
        end = config.physical_pose(3.5)
        self.assertTrue(press.force_controlled and end.force_controlled)
        self.assertEqual(press.normal_force_n, 4.0)
        self.assertEqual(end.normal_force_n, 4.0)
        # The load is reached by the end of the press and never moves again.
        forces = [
            config.physical_pose(float(t)).normal_force_n
            for t in case.frame_times
            if t >= 2.0
        ]
        self.assertEqual(set(forces), {4.0})
        # The slide is 4 mm along the cylinder's own axis, and only that axis,
        # at an unbroken 5 mm/s from the end of the acceleration ramp.
        self.assertAlmostEqual(end.y_m, 0.004)
        for at, expected in ((2.5, 0.0005), (2.8, 0.002), (3.0, 0.003), (3.2, 0.004)):
            self.assertAlmostEqual(config.physical_pose(at).y_m, expected, places=9)
        self.assertEqual({config.physical_pose(float(t)).x_m for t in case.frame_times}, {0.0})
        # Travel is an outcome here; nothing prescribes it after the handover.
        self.assertEqual(press.depth_m, 0.0)

    def test_the_cylinder_suites_record_checkpoints_rather_than_every_substep(self):
        """A slide bisects to a few 1e-5 s; the whole path will not fit in a file."""
        for name in CASES:
            with self.subTest(name):
                config = load(name)
                self.assertEqual(config.solver.result_substeps, "each_checkpoint")
                self.assertEqual(
                    config.specification.suite["contact_acceptance"]["scope"],
                    "all_recorded_frames_and_solved_checkpoints",
                )
        # The slab suites are unchanged, and an unknown value is refused.
        slab = Config.load(ROOT / "configs/material_plane_slide/rigid_reference.json")
        self.assertEqual(slab.solver.result_substeps, "every_substep")
        with self.assertRaisesRegex(ValueError, "result_substeps"):
            load("cylinder_20mm").with_solver(result_substeps="sometimes")

    def test_a_protocol_that_slides_along_both_axes_is_refused(self):
        case = load("cylinder_20mm").specification
        diagonal = PlaneCase(case.suite, case.case)
        for point in diagonal.suite["protocol"]["keyframes"]:
            point["x_m"] = point["y_m"]
        with self.assertRaisesRegex(ValueError, "one axis"):
            diagonal.validate()


class BandAcceptanceTests(unittest.TestCase):
    """A target smaller than the sensor is judged where the load actually is."""

    def coverage(self, case):
        mesh = gel_mesh(case, None)
        points = mesh.coordinates[mesh.surface_nodes]
        return ContactCoverage(case, points, mesh.surface_quads), points

    def state(self, points, band):
        force = np.zeros((len(points), 3))
        force[np.abs(points[:, 0]) <= band, 2] = -0.01
        return type("S", (), {"position_m": points, "contact_force_n": force})()

    def details(self, case, points, band):
        mesh = gel_mesh(case, None)
        centres = points[mesh.surface_quads][:, :, 0].mean(axis=1)
        pressure = np.where(np.abs(centres) <= band, 1e4, 0.0)
        faces = len(mesh.surface_quads)
        return {
            "pressure": np.repeat(pressure[:, None], 4, axis=1),
            "penetration": np.zeros((faces, 4)),
            "status": np.full((faces, 4), 3),
        }

    def test_load_inside_the_patch_passes_though_the_gel_is_wider(self):
        case = load("cylinder_20mm").specification
        coverage, points = self.coverage(case)
        pose = Config.load(
            ROOT / "configs/cylinder_press_slide/cylinder_20mm.json"
        ).physical_pose(2.0)
        record, _ = coverage.evaluate(
            self.state(points, 0.004), self.details(case, points, 0.004), pose
        )
        # The cylinder covers 8.7 mm of a 12.6 mm half width, so the whole-sensor
        # pair fails here; the band is what carries load and it is inside. Both
        # are recorded, and the setup says which pair the thresholds speak for.
        self.assertEqual(record["edge_margin_scope"], "loaded_sensor_nodes")
        self.assertLess(record["geometric_footprint_coverage_fraction"], 1.0)
        self.assertLess(record["minimum_plane_edge_margin_m"], 0.0)
        self.assertEqual(record["loaded_footprint_coverage_fraction"], 1.0)
        self.assertGreater(record["loaded_edge_margin_m"], 0.0005)
        self.assertGreater(record["loaded_sensor_node_count"], 0)
        coverage.validate(record)

    def test_load_running_off_the_patch_fails(self):
        case = load("cylinder_20mm").specification
        coverage, points = self.coverage(case)
        pose = Config.load(
            ROOT / "configs/cylinder_press_slide/cylinder_20mm.json"
        ).physical_pose(2.0)
        record, _ = coverage.evaluate(
            self.state(points, 0.012), self.details(case, points, 0.012), pose
        )
        self.assertLess(record["loaded_footprint_coverage_fraction"], 1.0)
        with self.assertRaisesRegex(RuntimeError, "does not cover the loaded region"):
            coverage.validate(record)

    def test_the_slab_suite_still_measures_every_sensor_node(self):
        case = PlaneCase.load(ROOT / "configs/material_plane_slide/rigid_reference.json")
        coverage, points = self.coverage(case)
        config = Config.load(ROOT / "configs/material_plane_slide/rigid_reference.json")
        record, _ = coverage.evaluate(
            type(
                "S",
                (),
                {
                    "position_m": points,
                    "contact_force_n": np.tile([0.0, 0.0, -0.01], (len(points), 1)),
                },
            )(),
            self.details(case, points, 1.0),
            config.physical_pose(2.0),
        )
        # A slab setup is gated on the whole surface, as it always has been, and
        # the loaded pair is recorded beside it rather than instead of it.
        self.assertEqual(record["edge_margin_scope"], "all_sensor_nodes")
        self.assertEqual(record["geometric_footprint_coverage_fraction"], 1.0)
        self.assertEqual(record["loaded_footprint_coverage_fraction"], 1.0)
        self.assertEqual(record["loaded_sensor_node_count"], len(points))
        margin, coverage_fraction, scope = coverage.geometry_gate(record)
        self.assertEqual(scope, "the sensor")
        self.assertEqual(margin, record["minimum_plane_edge_margin_m"])
        self.assertEqual(
            coverage_fraction, record["geometric_footprint_coverage_fraction"]
        )
        coverage.validate(record)


class MeshViewHelperTests(unittest.TestCase):
    def test_exterior_faces_are_the_boundary_of_the_body(self):
        from gelsight_ansys.config import Gel
        from gelsight_ansys.mesh import structured_mesh

        mesh = structured_mesh(Gel(elements=(3, 4, 2)))
        faces = exterior_faces(mesh.hexes)
        # Six sides of a 3 x 4 x 2 grid of bricks.
        self.assertEqual(len(faces), 2 * (3 * 4 + 3 * 2 + 4 * 2))
        self.assertEqual(len(np.unique(np.sort(faces, axis=1), axis=0)), len(faces))
        # Every interior node is absent; every boundary node appears. A
        # 4 x 5 x 3 node grid keeps 2 x 3 x 1 of them inside.
        self.assertEqual(len(np.unique(faces)), len(mesh.coordinates) - 2 * 3 * 1)

    def test_contour_limits_step_in_ones_twos_and_fives(self):
        self.assertEqual(nice_ceiling(0.34), 0.5)
        self.assertEqual(nice_ceiling(1.0), 1.0)
        self.assertEqual(nice_ceiling(1.01), 2.0)
        self.assertEqual(nice_ceiling(230.0), 500.0)
        self.assertEqual(nice_ceiling(0.0), 1.0)
        self.assertEqual(nice_ceiling(math.nan), 1.0)


if __name__ == "__main__":
    unittest.main()


class MeshFrameTests(unittest.TestCase):
    """Frames written while a run solves have to be assemblable afterwards."""

    def view(self, directory, scale=1.0):
        from dataclasses import replace

        from gelsight_ansys.config import Gel
        from gelsight_ansys.fem_view import MeshView
        from gelsight_ansys.mesh import structured_mesh

        config = load("cylinder_20mm")
        config = replace(
            config,
            gel=Gel(elements=(4, 4, 2)),
            visualization=replace(
                config.visualization, mesh_deformation_scale=scale
            ),
        )
        mesh = structured_mesh(config.gel)
        return (
            MeshView(
                directory,
                config,
                {
                    "gel_reference_m": mesh.coordinates,
                    "gel_hexes": mesh.hexes,
                    "surface_nodes": mesh.surface_nodes,
                },
            ),
            mesh,
        )

    def metric(self, time_s, depth):
        """The frame record a run writes, which is all the view reads."""
        return {
            "time_s": time_s,
            "depth_m": depth,
            "x_m": 0.0,
            "y_m": 0.0,
            "normal_force_n": 4.0,
        }

    def frame(self, mesh, pressure_pa, depth):
        surface = mesh.surface_nodes
        displacement = np.zeros_like(mesh.coordinates)
        displacement[:, 2] = -depth
        state = type(
            "S",
            (),
            {
                "reference_m": mesh.coordinates[surface],
                "displacement_m": displacement[surface],
                "quads": mesh.surface_quads,
                "contact_pressure_pa": np.full(len(mesh.surface_quads), pressure_pa),
            },
        )()
        return state, displacement

    def test_every_frame_is_the_same_size_and_limits_only_grow(self):
        import tempfile

        from PIL import Image

        from gelsight_ansys.artifacts import save_gif

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            view, mesh = self.view(directory)
            sizes, ceilings = [], []
            # Pressures three decades apart, so the colorbar labels change width.
            for index, (pressure, depth) in enumerate(
                ((12.0, 1e-6), (4.2e5, 4e-4), (9.0, 1e-6))
            ):
                state, displacement = self.frame(mesh, pressure, depth)
                metric = self.metric(0.1 * index, depth)
                ceilings.append(
                    view.render(index, state, metric, displacement)[
                        "contact_pressure_kpa"
                    ]
                )
                with Image.open(directory / "mesh" / f"frame_{index:04d}.png") as frame:
                    sizes.append(frame.size)
            view.close()
            self.assertEqual(len(set(sizes)), 1)
            # A later empty frame is drawn on the scale the run has reached.
            self.assertEqual(view.rescaled("contact_pressure_kpa", 0.0), ceilings[-1])
            # A ceiling is raised to cover a new peak and never comes back down.
            self.assertEqual(ceilings, sorted(ceilings))
            self.assertGreaterEqual(ceilings[1], 420.0)
            from gelsight_ansys.artifacts import ImageFiles

            save_gif(
                ImageFiles(sorted((directory / "mesh").glob("frame_*.png"))),
                directory / "mesh.gif",
                5,
            )
            with Image.open(directory / "mesh.gif") as gif:
                self.assertEqual(gif.n_frames, 3)

    def test_an_unloaded_first_frame_is_drawn_at_unity(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            view, _ = self.view(Path(temporary))
            # No peak has been seen, so there is no scale to read off the frame.
            self.assertEqual(view.rescaled("contact_pressure_kpa", 0.0), 1.0)
            self.assertEqual(view.rescaled("displacement_mm", 0.0), 1.0)
            view.close()

    def test_the_drawing_scale_never_reaches_the_recorded_solution(self):
        import tempfile


        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            view, mesh = self.view(directory, scale=25.0)
            state, displacement = self.frame(mesh, 1e4, 2e-4)
            before = displacement.copy()
            limits = view.render(0, state, self.metric(0.0, 2e-4), displacement)
            view.close()
            np.testing.assert_array_equal(displacement, before)
            # The contour reports the solved displacement, not the drawn one.
            self.assertEqual(limits["displacement_mm"], 0.2)


class OfflineMeshViewTests(unittest.TestCase):
    """A finished run holds everything the views are made of."""

    def build_run(self, directory):
        """The files a recorded run leaves behind, for two frames."""
        import json
        from dataclasses import replace

        from gelsight_ansys.config import Gel
        from gelsight_ansys.contracts import SurfaceState
        from gelsight_ansys.mesh import structured_mesh

        config = replace(load("cylinder_20mm"), gel=Gel(elements=(6, 6, 3)))
        mesh = structured_mesh(config.gel)
        target, _ = textured_target(config.specification, 0.0)
        (directory / "config.json").write_text(
            json.dumps(config.to_dict()), encoding="utf-8"
        )
        np.savez_compressed(
            directory / "solid_mesh.npz",
            gel_reference_m=mesh.coordinates,
            gel_hexes=mesh.hexes,
            surface_nodes=mesh.surface_nodes,
            target_reference_m=target,
        )
        faces, surface = len(mesh.surface_quads), mesh.surface_nodes
        frames = []
        for index, depth in enumerate((0.0, 3e-4)):
            displacement = np.zeros_like(mesh.coordinates)
            displacement[:, 2] = -depth
            SurfaceState(
                0.1 * index,
                mesh.coordinates[surface],
                displacement[surface],
                mesh.triangles,
                mesh.surface_quads,
                np.zeros((len(surface), 3)),
                np.full(faces, 5e4 * index),
                np.zeros(faces),
                np.zeros(faces),
                np.zeros(3),
                np.zeros(3),
                np.zeros(3),
                np.zeros(3),
                surface + 1,
            ).save(directory / "states" / f"frame_{index:04d}.npz")
            np.savez_compressed(
                directory / "bodies" / f"frame_{index:04d}.npz",
                gel_displacement_m=displacement,
            )
            frames.append(
                {
                    "time_s": 0.1 * index,
                    "depth_m": depth,
                    "x_m": 0.0,
                    "y_m": 0.0,
                    "twist_rad": 0.0,
                    "normal_force_n": 4.0 * index,
                    "platen_control": "load",
                    "commanded_normal_force_n": 4.0,
                }
            )
        (directory / "summary.json").write_text(
            json.dumps({"frames": frames}), encoding="utf-8"
        )

    def test_a_recorded_run_can_be_drawn_again_without_a_solver(self):
        import contextlib
        import io
        import tempfile

        from PIL import Image

        from gelsight_ansys.batch.render_mesh_views import main

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for folder in ("states", "bodies"):
                (directory / folder).mkdir()
            self.build_run(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--run", str(directory), "--deformation-scale", "5"])
            written = sorted((directory / "mesh").glob("frame_*.png"))
            self.assertEqual(len(written), 2)
            with Image.open(directory / "mesh.gif") as gif:
                self.assertEqual(gif.n_frames, 2)
