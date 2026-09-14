"""Resolution and safe queue publication contracts, without an ANSYS checkout."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from test_simulation import flat_state

from gelsight_ansys.config import Config
from gelsight_ansys.surface import Markers, image_coordinates

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gelsight_ansys.batch.audit_examples import audit
from gelsight_ansys.batch.render_queue import (
    SolverWatchdog,
    discover,
    replace_export,
    retire_removed_jobs,
)


class RenderQueueTests(unittest.TestCase):
    def test_resolution_preserves_material_markers_and_fov(self):
        original = Config()
        scaled = original.with_render_scale(4)
        self.assertEqual((scaled.camera.width_px, scaled.camera.height_px), (1280, 960))
        self.assertEqual(scaled.gel, original.gel)
        self.assertEqual(scaled.trajectory, original.trajectory)
        self.assertEqual(scaled.camera.fov_width_m, original.camera.fov_width_m)
        self.assertEqual(scaled.camera.fov_height_m, original.camera.fov_height_m)
        state = flat_state()
        a = Markers(
            state, original.optics.marker_spacing_m, original.camera, original.optics
        )
        b = Markers(state, scaled.optics.marker_spacing_m, scaled.camera, scaled.optics)
        np.testing.assert_allclose(a.reference_m, b.reference_m, rtol=0, atol=1e-15)
        state.displacement_m[:, 0] = 0.00003
        state.displacement_m[:, 2] = -0.0004
        old_pixels = image_coordinates(a.positions(state), original.camera)
        new_pixels = image_coordinates(b.positions(state), scaled.camera)
        np.testing.assert_allclose(new_pixels, (old_pixels + 0.5) * 4 - 0.5, atol=1e-10)
        for invalid in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                original.with_render_scale(invalid)

    def test_scaled_json_config_audits_without_tuple_list_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preset = root / "preset.json"
            preset.write_text(json.dumps(Config().to_dict()))
            folder = root / "example"
            folder.mkdir()
            scaled = Config.load(preset).with_render_scale(4)
            (folder / "config.json").write_text(json.dumps(scaled.to_dict()))
            # A semantically equal config must advance to checking the actual data.
            with self.assertRaises(FileNotFoundError):
                audit(folder, preset, render_scale=4)
            data = scaled.to_dict()
            data["material"]["young_pa"] *= 2
            (folder / "config.json").write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "does not match"):
                audit(folder, preset, render_scale=4)

    def test_a_skipped_preset_is_recorded_and_not_run(self):
        import io
        from contextlib import redirect_stdout

        from gelsight_ansys.batch.render_queue import main, mark_skipped

        with redirect_stdout(io.StringIO()) as out:
            main(["--work", "/tmp/q", "--examples", "/tmp/e", "--list-only",
                  "--skip", "sphere_press"])
        listed = json.loads(out.getvalue())["jobs"]
        skipped = next(j for j in listed if j["name"] == "sphere_press")
        self.assertEqual(skipped["status"], "skipped")
        self.assertTrue(all(j["status"] == "pending" for j in listed if j["name"] != "sphere_press"))
        # A pass stays a pass: skipping never demotes exported work.
        jobs = [{"name": "a", "status": "passed"}, {"name": "b", "status": "failed", "error_type": "X"}]
        mark_skipped(jobs, ["a", "b"])
        self.assertEqual([j["status"] for j in jobs], ["passed", "skipped"])
        self.assertNotIn("error_type", jobs[1])

    def test_plane_and_indenter_configs_are_all_discovered(self):
        root = Path(__file__).resolve().parents[1]
        jobs, blocked = discover(root / "configs", 4)
        self.assertEqual(len(jobs), 14)
        self.assertEqual(blocked, [])
        self.assertTrue(all(j["resolution"] == [1280, 960] for j in jobs))
        self.assertEqual(sum(j["kind"] == "plane" for j in jobs), 7)
        # Every plane preset shares one protocol, so they share a frame count.
        expected = len(
            Config.load(root / "configs/material_plane_slide/soft_rubber.json").trajectory
        )
        self.assertTrue(
            all(j["frame_count"] == expected for j in jobs if j["kind"] == "plane")
        )
        self.assertEqual(jobs[0]["name"], "sphere_press")

    def test_removed_presets_cannot_resume_and_history_is_preserved(self):
        root = Path(__file__).resolve().parents[1]
        jobs, blocked = discover(root / "configs", 4)
        names = {job["name"] for job in jobs}
        self.assertTrue({"flat_twist", "imported_rigid_slide"}.isdisjoint(names))
        self.assertIn("plane_soft_rubber_press_slide", names)
        self.assertEqual(blocked, [])
        old = [
            {"name": "flat_twist", "status": "failed"},
            {"name": "imported_rigid_slide", "status": "failed"},
        ]
        active = {"name": "plane_soft_rubber_press_slide", "status": "stopped"}
        state = {"status": "stopped", "jobs": [*old, active]}
        retire_removed_jobs(state, jobs)
        self.assertEqual(state["jobs"], [active])
        self.assertEqual(state["retired_jobs"], old)
        self.assertEqual(state["status"], "stopped")
        retire_removed_jobs(state, jobs)
        self.assertEqual(state["retired_jobs"], old)

    def test_watchdog_stops_only_an_orphaned_solving_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "example"
            run.mkdir()
            summary = run / "summary.json"
            summary.write_text(json.dumps({"status": "running", "phase": "solving"}))
            watcher = SolverWatchdog(root)
            child = Mock()
            fake = SimpleNamespace(
                process_iter=lambda attrs: [],
                NoSuchProcess=ProcessLookupError,
                AccessDenied=PermissionError,
            )
            with (
                patch.dict(sys.modules, {"psutil": fake}),
                patch(
                    "gelsight_ansys.batch.render_queue.time.monotonic",
                    side_effect=[0.0, 121.0],
                ),
            ):
                watcher.check(child)
                child.terminate.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "orphaned"):
                    watcher.check(child)
            child.terminate.assert_called_once()
            child.reset_mock()
            summary.write_text(json.dumps({"status": "running", "phase": "report"}))
            with patch.dict(sys.modules, {"psutil": fake}):
                watcher.check(child)
            child.terminate.assert_not_called()

    def test_watchdog_identifies_solver_by_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "example"
            run.mkdir()
            (run / "summary.json").write_text(
                json.dumps({"status": "running", "phase": "solving"})
            )
            process = Mock()
            process.info = {
                "name": "ANSYS.exe",
                "cmdline": ["ANSYS.exe", "-i", ".__tmp__.inp"],
            }
            process.cwd.return_value = str(run / "solver")
            fake = SimpleNamespace(
                process_iter=lambda attrs: [process],
                NoSuchProcess=ProcessLookupError,
                AccessDenied=PermissionError,
            )
            watcher = SolverWatchdog(root)
            watcher.absent_since = -1000.0
            child = Mock()
            with patch.dict(sys.modules, {"psutil": fake}):
                watcher.check(child)
            self.assertIsNone(watcher.absent_since)
            child.terminate.assert_not_called()
            # Another job's live ANSYS must not mask this job's vanished solver.
            process.cwd.return_value = str(root / "unrelated" / "solver")
            watcher.absent_since = 0.0
            with (
                patch.dict(sys.modules, {"psutil": fake}),
                patch(
                    "gelsight_ansys.batch.render_queue.time.monotonic", return_value=121.0
                ),
                self.assertRaisesRegex(RuntimeError, "orphaned"),
            ):
                watcher.check(child)
            child.terminate.assert_called_once()

    def test_failed_export_promotion_restores_previous_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work, destination = root / "work", root / "examples"
            staged, old = work / "staging/example", destination / "example"
            staged.mkdir(parents=True)
            old.mkdir(parents=True)
            (staged / "preview.png").write_bytes(b"new")
            (old / "preview.png").write_bytes(b"old")
            rename = Path.rename

            def fail_promotion(path, target):
                if path == staged:
                    raise OSError("simulated promotion failure")
                return rename(path, target)

            with (
                patch.object(Path, "rename", fail_promotion),
                self.assertRaises(OSError),
            ):
                replace_export(staged, destination, work)
            self.assertEqual((old / "preview.png").read_bytes(), b"old")
            self.assertEqual((staged / "preview.png").read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()


class SolverNameTests(unittest.TestCase):
    def test_the_watchdog_knows_the_solver_on_every_platform(self):
        from gelsight_ansys.batch.render_queue import is_mapdl_process

        for name in ("ansys.exe", "ANSYS252.exe", "ansys252", "ansys251", "ansys"):
            self.assertTrue(is_mapdl_process(name), name)
        for name in ("python.exe", "ansysedt.exe", "", None, "mapdl"):
            self.assertFalse(is_mapdl_process(name), name)

    def test_a_linux_solver_in_the_owned_directory_is_not_reported_missing(self):
        from gelsight_ansys.batch.render_queue import SolverWatchdog

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            run.mkdir()
            (run / "summary.json").write_text(
                json.dumps({"status": "running", "phase": "solving"})
            )
            process = Mock()
            process.info = {"name": "ansys252", "cmdline": ["ansys252"]}
            process.cwd.return_value = str(run / "solver")
            fake = SimpleNamespace(
                process_iter=lambda attrs: [process],
                NoSuchProcess=ProcessLookupError,
                AccessDenied=PermissionError,
            )
            watchdog = SolverWatchdog(root)
            watchdog.absent_since = 0
            child = Mock()
            with (
                patch.dict(sys.modules, {"psutil": fake}),
                patch(
                    "gelsight_ansys.batch.render_queue.time.monotonic",
                    return_value=121,
                ),
            ):
                watchdog.check(child)
            child.terminate.assert_not_called()
            self.assertIsNone(watchdog.absent_since)
