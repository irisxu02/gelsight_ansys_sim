"""One statement of the setup, and one statement of what a finished dataset is."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from gelsight_ansys.config import Config
from gelsight_ansys.plane_mechanics import AnsysPlane
from gelsight_ansys.run_contract import (
    completion_status,
    dataset_errors,
    require_dataset,
)
from gelsight_ansys.simulation_config import plane_consistency_errors

ROOT = Path(__file__).resolve().parents[1]
PRESETS = sorted(
    p for p in (ROOT / "configs/material_plane_slide").glob("*.json")
    if p.name != "suite.json"
)
RUBBER = ROOT / "configs/material_plane_slide/soft_rubber.json"


class SuiteIsTheSourceTests(unittest.TestCase):
    """A resolved plane run states each setting once, and it is the setup's."""

    def test_every_shipped_preset_agrees_with_its_setup(self):
        self.assertTrue(PRESETS)
        for path in PRESETS:
            with self.subTest(preset=path.name):
                config = Config.load(path)
                self.assertEqual(plane_consistency_errors(config), [])
                # A rendered run keeps the agreement at any whole scale.
                self.assertEqual(
                    plane_consistency_errors(config.with_render_scale(4)), []
                )

    def test_a_resolved_field_that_drifts_from_the_setup_is_rejected(self):
        config = Config.load(RUBBER)
        for field, value, expected in (
            ("indenter", replace(config.indenter, stiffness_factor=1.0), "indenter"),
            ("solver", replace(config.solver, force_tolerance=0.05), "solver.force_tolerance"),
            ("gel", replace(config.gel, thickness_m=0.005), "gel"),
        ):
            with self.subTest(field=field):
                drifted = replace(config, **{field: value})
                self.assertIn(expected, "; ".join(plane_consistency_errors(drifted)))
                with self.assertRaisesRegex(ValueError, "disagree with their setup"):
                    drifted.validate()

    def test_an_override_restates_the_setup_it_changes(self):
        config = Config.load(RUBBER)
        suite = lambda c: c.specification.suite  # noqa: E731
        tightened = config.with_solver(force_tolerance=0.001)
        self.assertEqual(suite(tightened)["solver"]["force_tolerance"], 0.001)
        subtracted = config.with_optics(render_mode="subtracted")
        self.assertEqual(
            suite(subtracted)["sensor"]["optics"]["render_mode"], "subtracted"
        )
        damped = config.with_contact_damping(stabilization_damping_normal=2e-3)
        self.assertEqual(
            suite(damped)["contact_numerics"]["stabilization_damping"]["normal_factor"],
            2e-3,
        )
        softer = config.with_gel_material(young_pa=120000)
        self.assertEqual(suite(softer)["sensor"]["material"]["young_pa"], 120000)
        # The configuration an override is taken from keeps its own values.
        self.assertEqual(suite(config)["solver"]["force_tolerance"], 0.005)

    def test_where_a_run_executes_is_not_part_of_the_setup(self):
        """Cores and GPU say where, not what; the setup states neither."""
        config = Config.load(RUBBER)
        allocated = config.with_solver(cores=3, gpu=True, require_gpu=True)
        self.assertEqual(allocated.solver.cores, 3)
        self.assertEqual(
            allocated.specification.suite["solver"]["cores"],
            config.specification.suite["solver"]["cores"],
        )
        self.assertEqual(plane_consistency_errors(allocated), [])

    def test_derived_pixel_geometry_cannot_be_set_by_hand(self):
        config = Config.load(RUBBER)
        with self.assertRaisesRegex(ValueError, "with_render_scale"):
            config.with_optics(marker_radius_px=12)
        with self.assertRaisesRegex(ValueError, "declared in its setup"):
            config.with_contact_refinement()

    def test_the_setup_declares_the_iteration_ceiling_it_gets(self):
        """It used to be raised silently, so a run disagreed with its own setup."""
        config = Config.load(RUBBER)
        self.assertEqual(
            config.solver.iterations,
            config.specification.suite["solver"]["iterations"],
        )


class OfflineReaderTests(unittest.TestCase):
    def test_reading_results_needs_geometry_and_no_session(self):
        config = Config.load(RUBBER)
        with tempfile.TemporaryDirectory() as temporary:
            solver = Path(temporary) / "solver"
            reader = AnsysPlane.offline_reader(config, solver, 197)
            built = AnsysPlane(config, solver)
            built.model_commands()
        self.assertIsNone(reader.mapdl)
        self.assertIsNone(reader.native_manifest)
        self.assertEqual(reader.nonmisc_base, 197)
        self.assertEqual(reader.last_timings, {})
        # The bodies are numbered exactly as the solved run's were.
        self.assertEqual(reader.pilot, built.pilot)
        self.assertEqual(len(reader.object_mesh.hexes), len(built.object_mesh.hexes))
        self.assertEqual(reader.object_offset, built.object_offset)
        self.assertEqual(reader.contact_start, built.contact_start)

    def test_a_native_material_can_be_read_back_without_its_libraries(self):
        """The result file already holds what the adapters computed."""
        fabric = Config.load(ROOT / "configs/material_plane_slide/fluffy_fabric.json")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "native plane adapters"):
                AnsysPlane(fabric, Path(temporary) / "solver")
            reader = AnsysPlane.offline_reader(fabric, Path(temporary) / "solver", 197)
        self.assertTrue(reader.needs_native)
        self.assertIsNone(reader.mapdl)


class DatasetContractTests(unittest.TestCase):
    def record(self, **overrides):
        summary = {
            "status": "passed",
            "frames": [{"time_s": 0.0}, {"time_s": 0.01}],
            "production_mesh": True,
            "complete_recorded_interval": True,
            "initialization_substeps": [{"time_s": -0.01}],
            "recorded_substeps": [{"time_s": 0.0}],
        }
        summary.update(overrides)
        config = {
            "config_kind": "resolved_material_plane_run",
            "trajectory": [{"time_s": 0.0}, {"time_s": 0.01}],
        }
        return summary, config

    def test_a_complete_plane_run_is_a_dataset(self):
        summary, config = self.record()
        self.assertEqual(dataset_errors(summary, config), [])
        require_dataset(summary, config)

    def test_every_way_a_run_falls_short_is_reported(self):
        for overrides, expected in (
            ({"status": "pilot_passed"}, "status"),
            ({"frames": [{"time_s": 0.0}]}, "1 frames recorded"),
            ({"production_mesh": False}, "production_mesh"),
            ({"complete_recorded_interval": False}, "complete_recorded_interval"),
            ({"initialization_substeps": []}, "initialization_substeps"),
            ({"recorded_substeps": []}, "recorded_substeps"),
            ({"frames": [{"time_s": 0.0}, {"time_s": 0.02}]}, "frame 1 is at"),
        ):
            with self.subTest(**overrides):
                summary, config = self.record(**overrides)
                self.assertIn(expected, "; ".join(dataset_errors(summary, config)))
                with self.assertRaisesRegex(ValueError, "not a complete dataset"):
                    require_dataset(summary, config)

    def test_a_sphere_run_is_judged_without_the_plane_rules(self):
        summary = {"status": "passed", "frames": [{"time_s": 0}]}
        config = {"indenter": {"shape": "sphere"}, "trajectory": [{"time_s": 0}]}
        self.assertEqual(dataset_errors(summary, config), [])

    def test_status_follows_what_the_run_actually_was(self):
        self.assertEqual(completion_status({"production_mesh": True, "complete_recorded_interval": True}), "passed")
        self.assertEqual(completion_status({"production_mesh": False, "complete_recorded_interval": True}), "pilot_passed")
        self.assertEqual(completion_status({"production_mesh": True, "complete_recorded_interval": False}), "pilot_passed")
        self.assertEqual(completion_status({"diagnostic_run": True, "production_mesh": True, "complete_recorded_interval": True}), "diagnostic_passed")

    def test_the_shipped_example_is_a_dataset_by_the_same_rule(self):
        folder = ROOT / "docs/examples/plane_rigid_short_slide_5n"
        summary = json.loads((folder / "summary.json").read_text())
        config = json.loads((folder / "config.json").read_text())
        self.assertEqual(dataset_errors(summary, config), [])


if __name__ == "__main__":
    unittest.main()
