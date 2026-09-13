"""Output thinning must retain mechanical history and checks on unsaved states."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from gelsight_ansys.config import Config
from gelsight_ansys.plane_mechanics import AnsysPlane
from gelsight_ansys.plane_pipeline import run_plane

ROOT = Path(__file__).resolve().parents[1]


class PlaneSamplingTests(unittest.TestCase):
    def run_fixture(self, reject_unsaved=False):
        config = Config.load(ROOT / "configs/material_plane_slide/rigid_reference.json")
        config = config.with_plane_sampling(sample_interval_s=0.02)
        solved, checked, rendered = [], [], []
        with tempfile.TemporaryDirectory() as tmp:
            template = AnsysPlane(config, Path(tmp) / "template")
            template.initial_pilot = np.array([0.0, 0.0, 0.003])
            reference = template.reference_state()

            class Model:
                mesh = template.mesh
                native_manifest = None
                object_mesh = None
                object_displacement = None
                contact_details = {}

                def __init__(self, *args):
                    self.step = 0
                    self.previous = -0.01

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def reference_state(self):
                    return deepcopy(reference)

                def solve_interval(self, pose):
                    solved.append(pose.time_s)
                    self.step += 1
                    for substep, at in enumerate(
                        ((pose.time_s + self.previous) / 2, pose.time_s), 1
                    ):
                        state = deepcopy(reference)
                        state.time_s = at
                        state.load_step, state.substep = self.step, substep
                        yield state, config.physical_pose(at), {"active": False}
                    self.previous = pose.time_s

            def evaluate(state, *args):
                return {"time_s": state.time_s, "active_bin_fraction": 1.0}, np.zeros(1)

            def validate(check):
                checked.append(check["time_s"])
                if reject_unsaved and np.isclose(check["time_s"], 0.01):
                    raise RuntimeError("Unsaved contact state failed coverage")

            coverage = Mock(evaluate=evaluate, validate=validate)
            renderer = SimpleNamespace(
                device="cpu", render=lambda *args: np.zeros((2, 2, 3), np.uint8)
            )
            optical = {
                "optical_normals": np.zeros((2, 2, 3)),
                "optical_valid_mask": np.ones((2, 2), bool),
                "optical_position_m": np.zeros((2, 2, 3)),
            }

            def render(directory, index, cfg, state, *args):
                rendered.append(state.time_s)
                return {
                    "time_s": state.time_s,
                    "load_step": state.load_step,
                    "normal_force_n": 0,
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
                if reject_unsaved:
                    with self.assertRaisesRegex(RuntimeError, "Unsaved contact"):
                        run_plane(
                            config,
                            Path(tmp),
                            stop_after_s=0.04,
                            progress=lambda message: None,
                        )
                    self.assertEqual(rendered, [0])
                    return
                _, summary = run_plane(
                    config, Path(tmp), stop_after_s=0.04, progress=lambda message: None
                )
            np.testing.assert_allclose(solved, [0, 0.01, 0.02, 0.03, 0.04])
            np.testing.assert_allclose(rendered, [0, 0.02, 0.04])
            self.assertEqual([f["load_step"] for f in summary["frames"]], [1, 3, 5])
            self.assertEqual(len(checked), 9)
            self.assertEqual(len(summary["recorded_substeps"]), 9)

    def test_thinning_frames_keeps_all_mechanical_states_checked(self):
        self.run_fixture()

    def test_contact_failure_between_saved_frames_is_not_hidden(self):
        self.run_fixture(reject_unsaved=True)


if __name__ == "__main__":
    unittest.main()
