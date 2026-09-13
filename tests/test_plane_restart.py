"""Restart preserves solved history and permits separation only during release."""

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gelsight_ansys.config import Config
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_coverage import ContactCoverage
from gelsight_ansys.plane_mechanics import AnsysPlane
from gelsight_ansys.plane_pipeline import run_plane
from gelsight_ansys.plane_restart import validate_plane_resume

ROOT = Path(__file__).resolve().parents[1]


def release_config(config):
    """Extend the shipped load-controlled protocol with a travel-driven release.

    Holding zero load cannot lift clear, so the release prescribes travel again
    and every keyframe from it onward carries release_travel_m.
    """
    case = PlaneCase(
        deepcopy(config.specification.suite), deepcopy(config.specification.case)
    )
    protocol = case.suite["protocol"]
    protocol.update(release=True, release_start_time_s=6.0, allow_recorded_lift_off=True)
    protocol["recorded_interval_s"][1] = 8.0
    protocol["phases"].append({"name": "release", "start_time_s": 6.0, "end_time_s": 8.0})
    last = protocol["keyframes"][-1]
    protocol["keyframes"] = [k for k in protocol["keyframes"] if k["time_s"] < 6.0] + [
        {**last, "time_s": 6.0, "normal_force_n": 5.0, "release_travel_m": 0.00029},
        {**last, "time_s": 7.6, "normal_force_n": 0.0, "release_travel_m": 0.0},
        {**last, "time_s": 8.0, "normal_force_n": 0.0, "release_travel_m": -0.0001},
    ]
    return replace(config, specification=case).with_plane_sampling(
        sample_interval_s=0.1, solve_interval_s=0.02
    )


class PlaneRestartTests(unittest.TestCase):
    def fixture(self, root):
        config = Config.load(
            ROOT / "configs/material_plane_slide/soft_rubber.json"
        ).with_plane_sampling(maximum_time_increment_s=0.02)
        model = AnsysPlane(config, root / "solver")
        model.initial_pilot = np.array([0.0, 0.0, 0.003])
        state = model.reference_state()
        state.time_s = 0.0
        state.load_step, state.substep, state.source = 1, 100, "ansys"
        (root / "states").mkdir()
        state.save(root / "states/frame_0000.npz")
        state.save(root / "unloaded_reference.npz")
        (root / "unloaded_reference.png").write_bytes(b"reference")
        np.savez(root / "solid_mesh.npz", gel_reference_m=model.mesh.coordinates)
        (root / "bodies").mkdir()
        np.savez(
            root / "bodies/frame_0000.npz",
            gel_displacement_m=np.zeros_like(model.mesh.coordinates),
        )
        for name in ("gel.rdb", "gel.ldhi", "gel.rst", "gel.r001"):
            (root / "solver" / name).write_bytes(b"test restart metadata")
        metric = {
            "time_s": 0.0,
            "load_step": 1,
            "substep": 100,
            "force_on_gel_n": [0, 0, 0],
            "normal_force_n": 0.0,
            "force_balance_error_n": 0.0,
            "pilot_force_error_n": 0.0,
            "raster_force_error_n": 0.0,
        }
        summary = {
            "status": "pilot_passed",
            "frames": [metric],
            "elapsed_s": 10.0,
            "initialization_substeps": [{"time_s": -0.02}],
            "recorded_substeps": [{"time_s": 0.0}],
            "production_mesh": True,
            "gpu_mechanics_verified": False,
        }
        (root / "config.json").write_text(json.dumps(config.to_dict()))
        (root / "summary.json").write_text(json.dumps(summary))
        return config, model, state

    def test_restart_accepts_future_sampling_and_release_but_rejects_changed_physics(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = self.fixture(root)
            continued = release_config(config)
            point, _ = validate_plane_resume(root, continued)
            self.assertEqual(
                (point.load_step, point.substep, point.time_s), (1, 100, 0.0)
            )
            self.assertEqual(len(continued.trajectory), 81)
            with self.assertRaisesRegex(ValueError, "solver"):
                validate_plane_resume(root, continued.with_solver(cores=3))
            case = PlaneCase(continued.specification.suite, continued.specification.case)
            case.suite["protocol"]["initialization"]["start_time_s"] = -3
            changed = replace(continued, specification=case)
            with self.assertRaisesRegex(ValueError, "preload history"):
                validate_plane_resume(root, changed)
            (root / "solver/gel.r001").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "nonlinear restart"):
                validate_plane_resume(root, continued)

    def test_diagnostic_resume_varies_convergence_but_not_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = self.fixture(root)
            continued = release_config(config)
            # The suite declares 0.005/L1; a sweep brackets it from there.
            relaxed = continued.with_solver(force_tolerance=0.001, force_norm=2)
            # Convergence controls are restated into the resumed database, so they
            # may change; the run is then marked as evidence, not as a dataset.
            with self.assertRaisesRegex(ValueError, "diagnostic resume"):
                validate_plane_resume(root, relaxed)
            _, summary = validate_plane_resume(root, relaxed, numerics_override=True)
            self.assertTrue(summary["diagnostic_run"])
            self.assertEqual(
                summary["numerics_overrides"][0]["changed"]["force_tolerance"],
                [0.005, 0.001],
            )
            # Allocation is not a convergence control.
            with self.assertRaisesRegex(ValueError, "only vary"):
                validate_plane_resume(
                    root, continued.with_solver(cores=3), numerics_override=True
                )
            # Real constants and element formulation live in gel.rdb.
            with self.assertRaisesRegex(ValueError, "requires a fresh run"):
                validate_plane_resume(
                    root,
                    continued.with_contact_damping(stabilization_damping_normal=2e-3),
                    numerics_override=True,
                )
            with self.assertRaisesRegex(ValueError, "requires a fresh run"):
                validate_plane_resume(
                    root,
                    continued.with_gel_material(formulation="mixed_up"),
                    numerics_override=True,
                )

    def test_acceptance_resume_varies_the_criterion_but_not_the_setup(self):
        """The gate is judged on results, so gel.rdb does not constrain it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = self.fixture(root)
            continued = release_config(config)
            scoped = replace(
                continued,
                specification=PlaneCase(
                    deepcopy(continued.specification.suite),
                    continued.specification.case,
                ),
            )
            bins = scoped.specification.suite["contact_acceptance"][
                "macroscopic_contact_bins"
            ]
            bins["region"] = "whole_sensor_surface"
            with self.assertRaisesRegex(ValueError, "contact_acceptance"):
                validate_plane_resume(root, scoped)
            _, summary = validate_plane_resume(root, scoped, acceptance_override=True)
            override = summary["acceptance_overrides"][0]
            # Frames written before the restart keep the rules they were checked
            # against, so the record says exactly where the boundary falls.
            self.assertEqual(override["resumed_at_time_s"], 0.0)
            self.assertEqual(
                override["from"]["macroscopic_contact_bins"]["region"],
                "camera_field_of_view",
            )
            self.assertEqual(
                override["to"]["macroscopic_contact_bins"]["region"],
                "whole_sensor_surface",
            )
            # An acceptance resume is not a licence to change the mechanics.
            with self.assertRaisesRegex(ValueError, "mechanical setup"):
                harder = replace(
                    scoped,
                    specification=PlaneCase(
                        deepcopy(scoped.specification.suite),
                        scoped.specification.case,
                    ),
                )
                harder.specification.suite["discretization"]["note"] = "changed"
                validate_plane_resume(root, harder, acceptance_override=True)

    def test_release_keeps_contact_checks_until_boundary_and_keeps_footprint_checks(self):
        config = release_config(
            Config.load(ROOT / "configs/material_plane_slide/soft_rubber.json")
        )
        self.assertTrue(config.specification.requires_contact(6.0))
        self.assertFalse(config.specification.requires_contact(6.02))
        checker = object.__new__(ContactCoverage)
        checker.rules = config.specification.suite["contact_acceptance"]
        empty = {
            "active_bin_fraction": 0.0,
            "total_repulsive_force_n": 0.0,
            "geometric_footprint_coverage_fraction": 1.0,
            "minimum_plane_edge_margin_m": 0.01,
        }
        with self.assertRaisesRegex(RuntimeError, "inactive"):
            checker.validate(empty)
        checker.validate(empty, require_contact=False)
        with self.assertRaisesRegex(RuntimeError, "edge margin"):
            checker.validate(
                {**empty, "minimum_plane_edge_margin_m": 0.0}, require_contact=False
            )
        config.specification.suite["protocol"]["allow_recorded_lift_off"] = False
        with self.assertRaisesRegex(ValueError, "lift-off"):
            config.validate()

    def test_pipeline_resumes_after_saved_pose_without_repeating_preload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, template, state = self.fixture(root)
            config = original.with_plane_sampling(
                sample_interval_s=0.02, solve_interval_s=0.02
            )
            solved, rendered = [], []

            class Model:
                mesh = template.mesh
                native_manifest = None
                object_mesh = None
                object_displacement = None
                contact_details = {}

                def __init__(self, *args, restart):
                    self.point = restart
                    self.step = restart.load_step

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def reference_state(self):
                    raise AssertionError(
                        "Resume must load the original unloaded reference"
                    )

                def solve_interval(self, pose):
                    solved.append(pose.time_s)
                    self.step += 1
                    current = deepcopy(state)
                    current.time_s, current.load_step, current.substep = (
                        pose.time_s,
                        self.step,
                        1,
                    )
                    yield current, pose, {"active": False}

            renderer = SimpleNamespace(
                device="cpu", render=lambda *args: np.zeros((2, 2, 3), np.uint8)
            )
            optical = {
                "optical_normals": np.zeros((2, 2, 3)),
                "optical_valid_mask": np.ones((2, 2), bool),
                "optical_position_m": np.zeros((2, 2, 3)),
            }
            coverage = Mock(
                evaluate=lambda *args: ({"active_bin_fraction": 1.0}, np.zeros(1))
            )

            def render(directory, index, cfg, current, *args):
                rendered.append((index, current.time_s))
                return {
                    "time_s": current.time_s,
                    "load_step": current.load_step,
                    "normal_force_n": 0.0,
                }

            with (
                patch("gelsight_ansys.plane_pipeline.AnsysPlane", Model),
                patch(
                    "gelsight_ansys.plane_pipeline.ContactCoverage", return_value=coverage
                ),
                patch(
                    "gelsight_ansys.plane_pipeline.prepare_optics",
                    return_value=(config, renderer),
                ),
                patch(
                    "gelsight_ansys.plane_pipeline.optical_surface", return_value=optical
                ),
                patch(
                    "gelsight_ansys.plane_pipeline.Markers",
                    return_value=SimpleNamespace(reference_m=np.zeros((1, 3))),
                ),
                patch(
                    "gelsight_ansys.plane_pipeline.image_coordinates",
                    return_value=np.zeros((1, 2)),
                ),
                patch(
                    "gelsight_ansys.plane_pipeline.render_plane_frame", side_effect=render
                ),
                patch("gelsight_ansys.run_services.build_report"),
            ):
                _, summary = run_plane(
                    config,
                    root,
                    resume_directory=root,
                    stop_after_s=0.04,
                    progress=lambda _: None,
                )
            self.assertEqual(solved, [0.02, 0.04])
            self.assertEqual(rendered, [(1, 0.02), (2, 0.04)])
            self.assertEqual([f["load_step"] for f in summary["frames"]], [1, 2, 3])
            self.assertEqual(len(summary["initialization_substeps"]), 1)
            self.assertEqual(summary["status"], "pilot_passed")
            self.assertGreaterEqual(summary["elapsed_s"], 10.0)


if __name__ == "__main__":
    unittest.main()
