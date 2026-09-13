"""Shared run failure handling and multi-resolution frame export contracts."""

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from test_simulation import flat_state, full_camera

from gelsight_ansys.config import Config
from gelsight_ansys.optics import Renderer
from gelsight_ansys.run_services import RunLifecycle, process_frame
from gelsight_ansys.surface import Markers


class RunServiceTests(unittest.TestCase):
    def test_failure_is_persisted_and_propagated_including_interrupts(self):
        for error in (RuntimeError("synthetic solver failure"), KeyboardInterrupt()):
            with (
                self.subTest(error=type(error).__name__),
                tempfile.TemporaryDirectory() as tmp,
            ):
                directory = Path(tmp)
                summary = {"frames": [{"time_s": 0.0}]}
                with self.assertRaises(type(error)):
                    with RunLifecycle(directory, summary, previous_elapsed=3):
                        raise error
                stored = json.loads((directory / "summary.json").read_text())
                self.assertEqual(stored["status"], "failed")
                self.assertEqual(stored["error_type"], type(error).__name__)
                self.assertEqual(stored["frames"], summary["frames"])
                self.assertGreaterEqual(stored["elapsed_s"], 3)
                self.assertIn(
                    type(error).__name__, (directory / "private/error.log").read_text()
                )

    def test_missing_gpu_evidence_cannot_produce_a_passed_report(self):
        config = Config().with_solver(gpu=True, require_gpu=True)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("gelsight_ansys.run_services.build_report") as report,
        ):
            summary = {"frames": [], "gpu_mechanics_verified": False}
            with self.assertRaisesRegex(RuntimeError, "GPU solver execution"):
                with RunLifecycle(Path(tmp), summary) as run:
                    run.finish(config)
            report.assert_not_called()
            self.assertEqual(summary["status"], "failed")

    def test_report_failure_cannot_mark_a_run_passed(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "gelsight_ansys.run_services.build_report",
                side_effect=OSError("synthetic disk failure"),
            ),
        ):
            summary = {"frames": []}
            with self.assertRaises(OSError):
                with RunLifecycle(Path(tmp), summary) as run:
                    run.finish(Config())
            self.assertEqual(summary["status"], "failed")

    def test_complete_and_pilot_runs_retain_distinct_status(self):
        for status in ("passed", "pilot_passed"):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as tmp,
                patch("gelsight_ansys.run_services.build_report") as report,
            ):
                summary = {"frames": []}
                with RunLifecycle(Path(tmp), summary) as run:
                    run.finish(Config(), status)
                self.assertEqual(summary["status"], status)
                report.assert_called_once()
                self.assertIn("report_elapsed_s", summary)

    def check_multiresolution_export(self, backend):
        base = Config()
        config = replace(
            base,
            camera=full_camera(projection="pinhole", standoff_m=3),
            optics=replace(
                base.optics, backend=backend, marker_margin_px=(2, 2), marker_radius_px=1
            ),
        ).validate()
        nominal = config.camera
        config = config.with_render_scale(2)
        state = flat_state()
        markers = Markers(
            state, config.optics.marker_spacing_m, config.camera, config.optics
        )
        state.displacement_m[:, 0] = 0.01
        images, projections = [], []
        with tempfile.TemporaryDirectory() as tmp:
            for store in (False, True):
                directory = Path(tmp) / str(store)
                directory.mkdir()
                renderer = Renderer(config)
                metric = process_frame(
                    directory,
                    0,
                    config,
                    state,
                    markers,
                    renderer,
                    config.trajectory[0],
                    {"active": False, "sparse_statistics": []},
                    {"max_indenter_deformation_m": 0.001},
                    field_camera=nominal,
                    store_optical_fields=store,
                    extra_fields={"macroscopic_bin_repulsive_force_n": np.array([20.0])},
                    extra_metrics={"contact_coverage": {"active_bin_fraction": 1.0}},
                )
                with Image.open(directory / "images/frame_0000.png") as image:
                    self.assertEqual(image.size, (34, 26))
                    images.append(np.array(image))
                with np.load(directory / "fields/frame_0000.npz") as fields:
                    self.assertEqual(fields["displacement_m"].shape, (13, 17, 3))
                    self.assertEqual(fields["rgb_difference_int16"].shape, (26, 34, 3))
                    self.assertEqual("optical_position_m" in fields, store)
                    np.testing.assert_allclose(
                        fields["macroscopic_bin_repulsive_force_n"], [20]
                    )
                    projections.append(fields["marker_flow_pixel"].copy())
                self.assertAlmostEqual(metric["normal_force_n"], 20)
                self.assertEqual(metric["contact_coverage"]["active_bin_fraction"], 1)
                self.assertEqual(metric["max_indenter_deformation_m"], 0.001)
        np.testing.assert_array_equal(images[0], images[1])
        np.testing.assert_array_equal(projections[0], projections[1])

    def test_nominal_force_fields_and_high_resolution_images_on_cpu(self):
        self.check_multiresolution_export("cpu")

    @unittest.skipUnless(os.environ.get("GELSIGHT_TEST_CUDA") == "1", "Opt-in CUDA check")
    def test_nominal_force_fields_and_high_resolution_images_on_cuda(self):
        self.check_multiresolution_export("cuda")


if __name__ == "__main__":
    unittest.main()
