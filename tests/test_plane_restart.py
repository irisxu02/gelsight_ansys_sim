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


FILE_SUMMARY = """
 RESTART FILE INFO - FILENAME: gel.r001
 LOADSTEP  SUBSTEP  BEGINNING TIME  CURRENT TIME    END TIME
     178      107       5.1550           5.1600       5.1600
 /INPUT FILE=    LINE=       0
 FINISH SOLUTION PROCESSING
 RESTART FILE INFO - FILENAME: gel.r002
 LOADSTEP  SUBSTEP  BEGINNING TIME  CURRENT TIME    END TIME
       2       13       2.0000           2.0200       2.0200
 ***** ROUTINE COMPLETED *****  CP =         0.000
"""


class RestartIndexTests(unittest.TestCase):
    """What ANTYPE,,REST can reach is what the index lists, not what is on disk."""

    def test_the_summary_is_read_as_load_step_substep_pairs(self):
        from gelsight_ansys.plane_mechanics import restart_points

        # Twenty-four .rNNN files were on disk when this was captured; the
        # index, rewritten by a /CLEAR on connect, remembered two of them.
        self.assertEqual(restart_points(FILE_SUMMARY), [(178, 107), (2, 13)])
        self.assertEqual(restart_points(""), [])
        self.assertNotIn((176, 60), restart_points(FILE_SUMMARY))


MONITOR = """
  LOAD   SUB-  NO.  NO.    TOTL  INCREMENT    TOTAL         VARIAB 1
  STEP   STEP ATTMP ITER   ITER  TIME/LFACT   TIME/LFACT    MONITOR

     1      1    1     4      4    0.20000E-01  0.20000E-01   0.0000
     3      6    2     3     40    0.10000E-01  2.0300        0.0000
     3      7    1     2     42    0.10000E-01  2.0400        0.0000
"""


class CheckpointResumeTests(unittest.TestCase):
    """The last converged load step is the one restart point MAPDL always keeps."""

    def test_the_monitor_names_the_last_converged_substep(self):
        from gelsight_ansys.solver_monitor import last_converged

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gel.mntr"
            path.write_text(MONITOR)
            self.assertEqual(last_converged(path), (3, 7, 2.04))
            path.write_text("banner only\n")
            with self.assertRaises(ValueError):
                last_converged(path)

    def test_only_a_finished_load_step_can_be_restarted_from(self):
        """A restart point is written at a load step's last substep, so a step
        interrupted part-way leaves nothing, however much of it converged."""
        from gelsight_ansys.solver_monitor import completed_steps

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gel.mntr"
            path.write_text(MONITOR)
            # Step 1 finished because step 3 began. Step 3 is the last one: it
            # finished only if its final substep reached a checkpoint.
            self.assertEqual(
                completed_steps(path, lambda at: False), [(1, 1, 0.02)]
            )
            self.assertEqual(
                completed_steps(path, lambda at: at == 2.04),
                [(1, 1, 0.02), (3, 7, 2.04)],
            )
            # Its converged substep 6 is not a restart point either way.
            self.assertNotIn(6, [substep for _, substep, _ in completed_steps(path, lambda at: True)])

    def test_a_checkpoint_resume_replays_what_was_never_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = PlaneRestartTests().fixture(root)
            continued = release_config(config)
            (root / "solver/gel.mntr").write_text(MONITOR)
            summary_path = root / "summary.json"
            summary = json.loads(summary_path.read_text())
            # The pipeline checked load step 3 up to substep 4; the solver went
            # on to substep 7 before the run stopped.
            summary["recorded_substeps"] = [
                {"time_s": 0.01, "load_step": 3, "substep": 4, "normal_force_n": 1.0}
            ]
            summary_path.write_text(json.dumps(summary))
            frame, _ = validate_plane_resume(root, continued)
            point, record = validate_plane_resume(root, continued, from_checkpoint=True)
            self.assertEqual((frame.load_step, frame.substep), (1, 100))
            self.assertEqual((point.load_step, point.substep), (3, 7))
            self.assertEqual(point.frame_index, frame.frame_index)
            # Solver time 2.04 with the preload starting at -2 s is 0.04 s.
            self.assertAlmostEqual(point.time_s, 0.04)
            self.assertEqual(point.replay_from_substep, 5)
            self.assertEqual(record["checkpoint_resumes"][-1]["replayed_from_substep"], 5)
            # Nothing to replay when the last checked substep is the last one.
            summary["recorded_substeps"][0]["substep"] = 7
            summary_path.write_text(json.dumps(summary))
            point, _ = validate_plane_resume(root, continued, from_checkpoint=True)
            self.assertIsNone(point.replay_from_substep)

    def test_nothing_is_replayed_without_a_replay_point(self):
        from gelsight_ansys.plane_restart import PlaneRestart

        point = PlaneRestart(3, 7, 0, 0.04)
        self.assertEqual(list(AnsysPlane.replay_load_step(Mock(), point)), [])

    def checkpoint_fixture(self, root, last_checked_substep):
        """A run whose last frame is at 0 s and whose solver reached 0.04 s."""
        config, model, state = PlaneRestartTests().fixture(root)
        config = config.with_plane_sampling(sample_interval_s=0.1, solve_interval_s=0.02)
        (root / "config.json").write_text(json.dumps(config.to_dict()))
        (root / "solver/gel.mntr").write_text(MONITOR)
        summary_path = root / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["recorded_substeps"] = [
            {
                "time_s": 0.04 if last_checked_substep == 7 else 0.03,
                "load_step": 3,
                "substep": last_checked_substep,
                "normal_force_n": 1.0,
            }
        ]
        summary_path.write_text(json.dumps(summary))
        return config, model, state

    def test_solved_history_is_compared_through_the_checkpoint_not_the_last_frame(self):
        """The result file holds the loading up to the checkpoint; none of it may change."""
        for last_checked in (4, 7):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(last_checked=last_checked):
                root = Path(tmp)
                config, _, _ = self.checkpoint_fixture(root, last_checked)
                changed = deepcopy(config)
                # The next keyframe after the last frame, before the checkpoint.
                changed.specification.suite["protocol"]["keyframes"][1]["normal_force_n"] = 4.0
                changed = changed.with_plane_sampling()
                self.assertNotEqual(
                    config.physical_pose(0.04).normal_force_n,
                    changed.physical_pose(0.04).normal_force_n,
                )
                # Judged against the last frame alone, the change is invisible.
                self.assertEqual(config.physical_pose(0.0), changed.physical_pose(0.0))
                with self.assertRaisesRegex(ValueError, "solved motion history"):
                    validate_plane_resume(root, changed, from_checkpoint=True)
                validate_plane_resume(root, config, from_checkpoint=True)

    def test_a_transient_window_that_shaped_solved_substeps_cannot_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = self.checkpoint_fixture(root, 7)
            changed = deepcopy(config)
            protocol = changed.specification.suite["protocol"]
            protocol["transient"]["windows"].insert(
                0,
                {
                    "start_time_s": 0.02,
                    "end_time_s": 0.1,
                    "time_increment_s": 0.01,
                    "inertia": True,
                },
            )
            changed = changed.with_plane_sampling()
            with self.assertRaisesRegex(ValueError, "time integration of solved history"):
                validate_plane_resume(root, changed, from_checkpoint=True)
            # A window entirely after the checkpoint is a future decision.
            later = deepcopy(changed)
            later.specification.suite["protocol"]["transient"]["windows"][0].update(
                start_time_s=0.06, end_time_s=0.1
            )
            later = later.with_plane_sampling()
            validate_plane_resume(root, later, from_checkpoint=True)

    def restored_set(self, point, model, drift=0.0):
        """What MAPDL answers about the result set a restart landed on.

        Its time is the result file's own, which is not the rounded value the
        solution monitor printed; drift stands for that difference.
        """

        def answer(entity, number, item, kind):
            return {
                "TIME": point.time_s + model.time_offset + drift,
                "LSTP": point.load_step,
                "SBST": point.substep,
            }[kind]

        return answer

    def test_a_restart_is_identified_by_its_substep_not_its_printed_time(self):
        """The monitor prints five digits; a mid-step checkpoint is named to that."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, model, state = self.checkpoint_fixture(root, 7)
            point, _ = validate_plane_resume(root, config, from_checkpoint=True)
            np.savez(
                root / "solid_mesh.npz",
                gel_reference_m=model.mesh.coordinates,
                gel_hexes=model.mesh.hexes,
            )
            model.parameters = {"PLANE_NMISC": 197}
            model.command_block = Mock(
                return_value=FILE_SUMMARY.replace("178      107", "  3        7")
            )
            later = deepcopy(state)
            later.displacement_m[:, 2] = -1e-5
            model.extract_saved_result = Mock(return_value=later)
            model.achieved_travel = Mock(return_value=1e-5)

            def restore(drift, substep=None):
                model.resume_point = replace(point, substep=substep or point.substep)
                model.mapdl = Mock()
                model.mapdl.parameters = {"PLANE_NMISC": 197}
                model.mapdl.get_value.side_effect = self.restored_set(
                    point, model, drift
                )
                with patch(
                    "gelsight_ansys.rst_contact.ContactResult", return_value=Mock()
                ):
                    model.restore_model()

            # The result file's instant differs from the printed one; accepted,
            # and the run continues on the exact instant rather than the name.
            restore(1.7e-5)
            self.assertAlmostEqual(model.resume_point.time_s, point.time_s + 1.7e-5)
            self.assertAlmostEqual(model.previous_solver_time, point.time_s + model.time_offset + 1.7e-5)
            # A different substep than the one asked for is refused outright,
            # which the loose time comparison alone could not catch: substeps
            # here are tens of microseconds apart.
            with self.assertRaisesRegex(RuntimeError, "not the requested"):
                restore(0.0, substep=6)
            # A time far from the name means the restart landed somewhere else.
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                restore(0.01)

    def test_a_fully_checked_checkpoint_past_the_last_frame_is_not_compared_to_it(self):
        """Identity, not replay work, decides whether the point is a saved frame."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, model, state = self.checkpoint_fixture(root, 7)
            point, _ = validate_plane_resume(root, config, from_checkpoint=True)
            self.assertIsNone(point.replay_from_substep)
            self.assertEqual((point.load_step, point.substep), (3, 7))
            self.assertEqual(point.frame_index, 0)
            np.savez(
                root / "solid_mesh.npz",
                gel_reference_m=model.mesh.coordinates,
                gel_hexes=model.mesh.hexes,
            )
            model.resume_point = point
            model.mapdl = Mock()
            model.mapdl.get_value.side_effect = self.restored_set(point, model)
            model.mapdl.parameters = {"PLANE_NMISC": 197}
            model.command_block = Mock(return_value=FILE_SUMMARY.replace("178      107", "  3        7"))
            later = deepcopy(state)
            later.displacement_m[:, 2] = -1e-5
            model.extract_saved_result = Mock(return_value=later)
            model.achieved_travel = Mock(return_value=1e-5)
            with patch("gelsight_ansys.rst_contact.ContactResult", return_value=Mock()):
                model.restore_model()
            restored, _, _ = model.restored()
            self.assertEqual((restored.load_step, restored.substep), (3, 7))
            # The same point named as the saved frame itself is checked against it.
            frame_point = replace(point, load_step=1, substep=100, time_s=0.0)
            model.resume_point = frame_point
            model.mapdl.get_value.side_effect = self.restored_set(frame_point, model)
            model.command_block = Mock(return_value=FILE_SUMMARY.replace("178      107", "  1      100"))
            with (
                patch("gelsight_ansys.rst_contact.ContactResult", return_value=Mock()),
                self.assertRaises(AssertionError),
            ):
                model.restore_model()


class ResumeCopyTests(unittest.TestCase):
    def test_the_solver_lock_of_an_interrupted_run_is_not_carried_over(self):
        """PyMAPDL refuses to launch over a lock file, and an interrupted run has one."""
        from gelsight_ansys.pipeline import resume_run

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            root = Path(tmp)
            config, _, _ = PlaneRestartTests().fixture(root)
            (root / "solver/gel.lock").write_text("pid")
            (root / "solver/gel.rdb").write_bytes(b"db")
            seen = {}

            def fake_run(cfg, output, *args, **kwargs):
                seen["directory"] = Path(kwargs["resume_directory"])
                return seen["directory"], {}

            with patch("gelsight_ansys.pipeline.run", fake_run):
                resume_run(root, Path(out), None, config=release_config(config))
            copied = seen["directory"] / "solver"
            self.assertTrue((copied / "gel.rdb").is_file())
            self.assertFalse((copied / "gel.lock").exists())


class ResumedSessionTests(unittest.TestCase):
    """A resumed session must connect to the files as the run left them."""

    def launch_kwargs(self, restart):
        import types

        recorded = {}

        def fake_launch(**kwargs):
            recorded.update(kwargs)
            return Mock()

        fake = types.SimpleNamespace(launch_mapdl=fake_launch)
        config = Config.load(ROOT / "configs/material_plane_slide/rigid_reference.json")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ANSYS252.exe"
            exe.write_bytes(b"")
            with patch.dict(sys.modules, {"ansys": types.SimpleNamespace(mapdl=types.SimpleNamespace(core=fake)),
                                          "ansys.mapdl": types.SimpleNamespace(core=fake),
                                          "ansys.mapdl.core": fake}), patch.object(
                AnsysPlane, "build", lambda self: None
            ):
                with AnsysPlane(config, Path(tmp) / "solver", executable=exe, restart=restart):
                    pass
        return recorded

    def test_a_fresh_session_clears_and_a_resumed_one_does_not(self):
        # PyMAPDL's /CLEAR on connect rewrites gel.ldhi and truncates gel.rst,
        # which is the restart set a resume exists to continue from.
        self.assertTrue(self.launch_kwargs(None)["clear_on_connect"])
        from gelsight_ansys.plane_restart import PlaneRestart

        point = PlaneRestart(176, 60, 32, 3.15)
        self.assertFalse(self.launch_kwargs(point)["clear_on_connect"])


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
            # 0.1 s frames over 8 s, joined with the slide window's own 0.01 s.
            coarse = np.linspace(0, 8, 81)
            w = continued.specification.transient_windows[0]
            fine = np.linspace(w["start_time_s"], w["end_time_s"],
                               round((w["end_time_s"] - w["start_time_s"]) / w["sample_interval_s"]) + 1)
            expected = len(np.unique(np.round(np.concatenate([coarse, fine]), 12)))
            self.assertEqual(len(continued.trajectory), expected)
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
                harder.specification.suite["discretization"][
                    "common_contact_surface_max_edge_m"
                ] = 0.0002
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

    def test_a_checkpoint_resume_renders_the_frame_at_its_restart_time(self):
        """A run that stopped after solving a frame's instant still owes that frame."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, template, state = self.fixture(root)
            config = original.with_plane_sampling(
                sample_interval_s=0.02, solve_interval_s=0.02
            )
            # The solver converged the 0.02 s checkpoint (load step 2) and the
            # pipeline checked all of it, but stopped before rendering the frame.
            (root / "solver/gel.mntr").write_text(
                "  LOAD SUB NO NO TOTL INCREMENT TOTAL V1\n\n"
                "     1    100    1    2    2    0.02  2.0000  0.0\n"
                "     2      5    1    2    7    0.004 2.0200  0.0\n"
            )
            summary_path = root / "summary.json"
            summary = json.loads(summary_path.read_text())
            summary["recorded_substeps"] = [
                {"time_s": 0.02, "load_step": 2, "substep": 5, "normal_force_n": 1.0}
            ]
            summary_path.write_text(json.dumps(summary))
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

                def restored(self):
                    current = deepcopy(state)
                    current.time_s, current.load_step, current.substep = (
                        self.point.time_s,
                        self.point.load_step,
                        self.point.substep,
                    )
                    return current, config.physical_pose(self.point.time_s), {"active": False}

                def replay_load_step(self, point):
                    return iter(())

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
                    from_checkpoint=True,
                )
            # Nothing is re-solved at 0.02: its frame comes from the restored state.
            self.assertEqual(solved, [0.04])
            self.assertEqual(rendered, [(1, 0.02), (2, 0.04)])
            self.assertEqual([f["load_step"] for f in summary["frames"]], [1, 2, 3])
            self.assertEqual(summary["checkpoint_resumes"][-1]["resumed_at_time_s"], 0.02)


class FillFrameTests(unittest.TestCase):
    """A solved-but-unwritten frame is found by its instant and kept in order."""

    def test_missing_frames_are_those_solved_without_a_recorded_metric(self):
        from gelsight_ansys.batch.fill_frame import missing_frame_indices

        frames = [{"time_s": 0.0}, {"time_s": 0.1}, {"time_s": 0.3}]
        times = np.array([0.0, 0.1, 0.2, 0.3, 0.4])
        # Frame 2 was solved and never recorded; frame 4 was never solved.
        self.assertEqual(missing_frame_indices(frames, times, 0.3), [2])

    def test_the_substep_is_the_one_converged_at_the_instant(self):
        from gelsight_ansys.batch.fill_frame import substep_at

        summary = {"recorded_substeps": [
            {"time_s": 3.195, "load_step": 185, "substep": 96},
            {"time_s": 3.2, "load_step": 186, "substep": 89},
        ]}
        self.assertEqual(substep_at(summary, 3.2), (186, 89))
        with self.assertRaisesRegex(ValueError, "No converged substep"):
            substep_at(summary, 3.21)

    def test_a_filled_frame_takes_its_place_by_time(self):
        from gelsight_ansys.batch.fill_frame import insert_frame

        frames = [{"time_s": 3.15}, {"time_s": 3.25}]
        self.assertEqual(insert_frame(frames, {"time_s": 3.2}), 1)
        self.assertEqual([f["time_s"] for f in frames], [3.15, 3.2, 3.25])


if __name__ == "__main__":
    unittest.main()


class FillFrameRenderingTests(unittest.TestCase):
    """A frame rendered after the run is measured against the run's reference."""

    def fill(self, root, summary_update, status_only=False):
        from gelsight_ansys.batch import fill_frame

        config, model, state = PlaneRestartTests().fixture(root)
        config = config.with_optics(backend="cpu")
        (root / "config.json").write_text(json.dumps(config.to_dict()))
        summary_path = root / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["recorded_substeps"] = [
            {"time_s": 0.0, "load_step": 1, "substep": 100, "normal_force_n": 1.0},
            {"time_s": 0.01, "load_step": 2, "substep": 5, "normal_force_n": 1.1},
        ]
        summary["mesh"] = {"contact_nonmisc_base": 197}
        summary.update(summary_update)
        summary_path.write_text(json.dumps(summary))
        captured = {}

        def render(directory, index, config, state, pose, model, markers, renderer, *rest):
            captured["reference"] = renderer.reference_rgb.copy()
            captured["index"] = index
            return {"time_s": pose.time_s, "load_step": state.load_step, "substep": state.substep}

        solved = deepcopy(state)
        solved.load_step, solved.substep = 2, 5
        reader = Mock()
        reader.converged_states = Mock(
            return_value=iter([(solved, config.physical_pose(0.01), {"active": False})])
        )
        reader.contact_details, reader.object_mesh, reader.object_displacement = {}, None, None
        coverage = Mock()
        coverage.evaluate.return_value = ({}, None)
        with (
            patch.object(fill_frame, "render_plane_frame", render),
            patch.object(fill_frame, "offline_model", return_value=reader),
            patch.object(fill_frame, "ContactCoverage", return_value=coverage),
            patch.object(fill_frame, "build_report"),
        ):
            filled = fill_frame.fill_missing_frames(root, progress=lambda _: None)
        return config, captured, filled, json.loads(summary_path.read_text())

    def test_the_unloaded_gel_is_rendered_before_the_filled_frame(self):
        from gelsight_ansys.contracts import SurfaceState
        from gelsight_ansys.optics import Renderer
        from gelsight_ansys.plane_pipeline import render_unloaded_reference

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, captured, filled, summary = self.fill(root, {})
            self.assertEqual([f["index"] for f in filled], [1])
            self.assertEqual(captured["index"], 1)
            reference = SurfaceState.load(root / "unloaded_reference.npz")
            expected = render_unloaded_reference(reference, config, Renderer(config, root))
            np.testing.assert_array_equal(captured["reference"], expected)
            self.assertEqual([f["time_s"] for f in summary["frames"]], [0.0, 0.01])

    def test_only_a_report_failure_is_promoted_and_by_the_normal_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A run that stopped solving keeps its failure whatever gets filled.
            *_, summary = self.fill(
                root,
                {
                    "status": "failed",
                    "error_type": "RuntimeError",
                    "phase": "solving",
                    "production_mesh": False,
                    "diagnostic_run": True,
                },
            )
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["error_type"], "RuntimeError")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A report failure on an incomplete diagnostic pilot: the report is
            # rebuilt, and the run is what it always was, not a passed dataset.
            *_, summary = self.fill(
                root,
                {
                    "status": "failed",
                    "error_type": "FileNotFoundError",
                    "phase": "report",
                    "production_mesh": False,
                    "diagnostic_run": True,
                },
            )
            self.assertEqual(summary["status"], "diagnostic_passed")
            self.assertFalse(summary["complete_recorded_interval"])
            self.assertNotIn("error_type", summary)


class LegacyRecordTests(unittest.TestCase):
    def test_reading_an_old_record_without_validation_reaches_the_migration(self):
        legacy = Config().to_dict()
        legacy["schema_version"] = 1
        legacy["name"] = "old record name"
        with self.assertRaises(ValueError):
            Config.from_dict(legacy)
        self.assertEqual(Config.from_dict(legacy, validate=False).name, "old record name")


class RefillTests(unittest.TestCase):
    def test_a_refilled_frame_is_found_missing_and_rendered_again(self):
        from gelsight_ansys.batch.fill_frame import missing_frame_indices

        frames = [{"time_s": 0.0}, {"time_s": 0.01}]
        self.assertEqual(missing_frame_indices(frames, np.array([0.0, 0.01]), 0.01), [])
        kept = [f for f in frames if abs(f["time_s"] - 0.01) > 1e-9]
        self.assertEqual(missing_frame_indices(kept, np.array([0.0, 0.01]), 0.01), [1])


class DocumentationIsNotPhysicsTests(unittest.TestCase):
    """Prose in a setup records why a number is what it is; it is not a setting."""

    def test_editing_a_note_does_not_make_a_run_unresumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = PlaneRestartTests().fixture(root)
            continued = release_config(config)
            validate_plane_resume(root, continued)
            annotated = deepcopy(continued)
            suite = annotated.specification.suite
            suite["solver"]["note"] = "measured on 14 September; see the run log"
            suite["contact_numerics"]["pinball_note"] = "rewritten"
            suite["discretization"]["note"] = "rewritten"
            annotated = annotated.with_plane_sampling()
            self.assertNotEqual(
                suite["solver"]["note"], continued.specification.suite["solver"]["note"]
            )
            validate_plane_resume(root, annotated)

    def test_a_setting_beside_the_prose_still_stops_the_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _, _ = PlaneRestartTests().fixture(root)
            continued = release_config(config)
            changed = deepcopy(continued)
            changed.specification.suite["discretization"]["note"] = "rewritten"
            changed.specification.suite["discretization"][
                "common_contact_surface_max_edge_m"
            ] = 0.0002
            changed = changed.with_plane_sampling()
            with self.assertRaisesRegex(ValueError, "mechanical setup"):
                validate_plane_resume(root, changed)
