"""Convergence controls, contact damping, and the diagnostics that explain a stall.

A hidden 1e-4/L2 override once replaced the setup's declared 0.005/L1 tolerance.
Once the near-incompressible slab carried real load the criterion was unreachable,
so Newton kept iterating past an already-converged state until contact
re-detection and the u-P volumetric constraint tore the specimen elements apart.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_solver_monitor import read_monitor, summarize

from gelsight_ansys.config import Config
from gelsight_ansys.plane_coverage import ContactCoverage
from gelsight_ansys.plane_mechanics import (
    AnsysPlane,
    contact_damping_commands,
    solution_control_commands,
)
from gelsight_ansys.plane_mesh import gel_mesh, slab_mesh
from gelsight_ansys.simulation_config import plane_solver

ROOT = Path(__file__).resolve().parents[1]
RUBBER = ROOT / "configs/material_plane_slide/soft_rubber.json"


def deck(config):
    """Render the APDL model deck without a licensed solver.

    build() writes the deck before asking MAPDL to read it back, so the post-input
    node count check is expected to fail against a mock and is not what is tested.
    """
    with tempfile.TemporaryDirectory() as tmp:
        model = AnsysPlane(config, Path(tmp))
        model.mapdl = MagicMock()
        model.mapdl.get_value.return_value = 0
        with unittest.mock.patch.object(AnsysPlane, "__exit__", lambda *a: None):
            try:
                model.build()
            except RuntimeError as error:
                assert "node import failed" in str(error), error
        return (Path(tmp) / "plane_model.inp").read_text().splitlines()


class PlaneNumericsTests(unittest.TestCase):
    def test_setup_tolerances_reach_the_solver_unchanged(self):
        case = Config.load(RUBBER).specification
        declared = case.suite["solver"]
        solver = plane_solver(case)
        self.assertEqual(solver.force_tolerance, declared["force_tolerance"])
        self.assertEqual(solver.force_norm, declared["force_norm"])
        self.assertEqual((solver.force_tolerance, solver.force_norm), (0.005, 1))
        # Only the iteration ceiling is raised above the declared value.
        self.assertEqual(solver.iterations, max(150, declared["iterations"]))
        for mode in ("cpu", "specified"):
            self.assertEqual(plane_solver(case, mode).force_tolerance, 0.005)

    def test_convergence_controls_are_emitted_and_restated(self):
        config = Config.load(RUBBER)
        self.assertIn("CNVTOL,F,,0.005,1,1e-6", solution_control_commands(config.solver))
        self.assertIn("CNVTOL,F,,0.005,1,1e-6", deck(config))
        loose = config.with_solver(force_tolerance=0.01, force_norm=2)
        self.assertIn("CNVTOL,F,,0.01,2,1e-6", deck(loose))
        # A resumed model is rebuilt from gel.rdb, so the controls are restated.
        source = (ROOT / "src/gelsight_ansys/plane_mechanics.py").read_text()
        restore = source[
            source.index("def restore_model") : source.index("def solve_interval")
        ]
        self.assertIn("solution_control_commands(self.config.solver)", restore)

    def test_diagnostics_name_the_failing_elements_only_when_requested(self):
        config = Config.load(RUBBER)
        self.assertIn("NLDIAG,NRRE,OFF", solution_control_commands(config.solver))
        on = solution_control_commands(
            config.with_solver(nonlinear_diagnostics=True).solver
        )
        self.assertIn("NLDIAG,NRRE,ON", on)
        self.assertIn("NLDIAG,CONT,ITER", on)

    def test_stabilization_damping_uses_the_damping_slots_not_the_squeal_pair(self):
        config = Config.load(RUBBER)
        self.assertEqual(contact_damping_commands(config.indenter, 2), [])
        self.assertNotIn("KEYOPT,2,15,", "".join(deck(config)))
        damped = config.with_contact_damping(
            stabilization_damping_normal=1e-3,
            stabilization_damping_tangential=1e-4,
            stabilization_damping_activation="always",
        )
        commands = contact_damping_commands(damped.indenter, 2)
        # 31/32 are FDMN/FDMT; 33/34 are the squeal damping pair.
        self.assertEqual(
            commands, ["RMODIF,1,31,0.001", "RMODIF,1,32,0.0001", "KEYOPT,2,15,3"]
        )
        lines = deck(damped)
        for command in commands:
            self.assertIn(command, lines)
        # Damping must not disable separation: KEYOPT(12) stays standard contact.
        self.assertIn("KEYOPT,2,12,0", lines)

    def test_setup_can_declare_damping_and_rejects_unknown_settings(self):
        from gelsight_ansys.simulation_config import (
            ConfigurationError,
            plane_contact_damping,
        )

        self.assertEqual(plane_contact_damping({}), {})
        self.assertEqual(
            plane_contact_damping(
                {"stabilization_damping": {"normal_factor": 1e-3, "activation": "always"}}
            ),
            {
                "stabilization_damping_normal": 1e-3,
                "stabilization_damping_tangential": None,
                "stabilization_damping_activation": "always",
            },
        )
        for bad in ({"nope": 1}, {"activation": "sometimes"}):
            with self.assertRaises(ConfigurationError):
                plane_contact_damping({"stabilization_damping": bad})

    def test_gel_formulation_switch_reaches_the_element_keyopt(self):
        config = Config.load(RUBBER)
        self.assertEqual(config.material.formulation, "displacement")
        self.assertIn("KEYOPT,1,6,0", deck(config))
        mixed = config.with_gel_material(formulation="mixed_up")
        self.assertIn("KEYOPT,1,6,1", deck(mixed))
        # The specimen already uses mixed u-P; the comparison must not disturb it.
        self.assertIn("KEYOPT,5,6,1", deck(config))
        self.assertIn("KEYOPT,5,6,1", deck(mixed))
        self.assertEqual(
            mixed.specification.suite["sensor"]["material"]["formulation"], "mixed_up"
        )

    def test_compact_specimen_keeps_the_declared_edge_margin_through_the_slide(self):
        compact = Config.load(
            ROOT / "configs/material_plane_slide/soft_rubber_compact.json"
        )
        baseline = Config.load(RUBBER)
        suite = compact.specification.suite
        rules = suite["contact_acceptance"]
        gel = suite["sensor"]["gel"]
        slide = max(p["x_m"] for p in suite["protocol"]["keyframes"])
        needed = 2 * (gel["width_m"] / 2 + rules["minimum_plane_edge_margin_m"] + slide)
        self.assertGreaterEqual(suite["specimen"]["width_m"], needed)
        mesh = gel_mesh(compact.specification, None)
        points = mesh.coordinates[mesh.surface_nodes]
        coverage = ContactCoverage(compact.specification, points, mesh.surface_quads)
        state = type("S", (), {"position_m": points})()
        faces = len(mesh.surface_quads)
        details = {
            "pressure": np.full((faces, 4), 1e4),
            "penetration": np.zeros((faces, 4)),
            "status": np.full((faces, 4), 3),
        }
        for at in (0.0, 5.0):
            record, _ = coverage.evaluate(state, details, compact.physical_pose(at))
            coverage.validate(record)
        # Same material, contact and protocol; only the clamped overhang changes.
        self.assertEqual(compact.specification.bulk, baseline.specification.bulk)
        self.assertEqual(
            compact.specification.case["contact"], baseline.specification.case["contact"]
        )
        self.assertEqual(
            compact.specification.suite["protocol"],
            baseline.specification.suite["protocol"],
        )
        shrunk = len(slab_mesh(compact.specification, 0.0).hexes)
        full = len(slab_mesh(baseline.specification, 0.0).hexes)
        self.assertLess(shrunk, full * 0.75)

    def test_monitor_parser_separates_a_healthy_solve_from_a_degrading_one(self):
        healthy = "\n".join(
            f"     1 {i:6d}    1     3 {3 * i:6d}    0.20000E-01  {0.02 * i:.5f}"
            for i in range(1, 21)
        )
        degrading = (
            healthy
            + "\n"
            + "\n".join(
                f"     2 {i:6d}    2 {10 + i:5d} {100 + i:6d}    0.70000E-02  {0.4 + 0.007 * i:.5f}"
                for i in range(1, 11)
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "a.mntr", Path(tmp) / "b.mntr"
            good.write_text("banner\n\n\n" + healthy + "\n")
            bad.write_text("banner\n\n\n" + degrading + "\n")
            clean = summarize(read_monitor(good))
            self.assertEqual(clean["retried_substeps"], 0)
            self.assertEqual(clean["mean_iterations_last"], 3)
            stalling = summarize(read_monitor(bad))
            self.assertEqual(stalling["retried_substeps"], 10)
            self.assertEqual(stalling["retried_at"][0]["load_step"], 2)
            self.assertGreater(
                stalling["mean_iterations_last"], 2 * stalling["mean_iterations_first"]
            )
            self.assertLess(
                stalling["smallest_time_increment"], stalling["largest_time_increment"]
            )

    def test_recorded_failure_shows_the_cost_trend_before_the_abort(self):
        monitor = sorted(
            (ROOT / "outputs").glob("soft_rubber_cycle_0p02/runs/*/solver/gel.mntr")
        )
        if not monitor:
            self.skipTest("Recorded failing run is not present in this checkout")
        summary = summarize(read_monitor(monitor[0]))
        self.assertGreater(
            summary["mean_iterations_last"], summary["mean_iterations_first"]
        )
        self.assertTrue(summary["retried_at"])


if __name__ == "__main__":
    unittest.main()


class ConvergenceSweepTests(unittest.TestCase):
    """Each variant must differ from the baseline in exactly one respect."""

    def resolve(self, variant, common):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch

        from gelsight_ansys.cli import main

        completed = (Path("unused"), {"status": "passed", "frames": []})
        with (
            patch("gelsight_ansys.pipeline.run", return_value=completed) as execute,
            redirect_stdout(io.StringIO()),
        ):
            code = main(
                [
                    "run",
                    "--config",
                    str(ROOT / variant["config"]),
                    *common,
                    *variant["arguments"],
                ]
            )
        self.assertEqual(code, 0, variant["name"])
        return execute.call_args.args[0]

    def signature(self, config):
        return {
            "tolerance": (config.solver.force_tolerance, config.solver.force_norm),
            "gel_formulation": config.material.formulation,
            "damping": tuple(contact_damping_commands(config.indenter, 2)),
            "specimen": (
                config.specification.suite["specimen"]["width_m"],
                config.specification.suite["specimen"]["length_m"],
            ),
            "material": json.dumps(config.specification.bulk, sort_keys=True),
            "contact": json.dumps(config.specification.case["contact"], sort_keys=True),
            "protocol": json.dumps(
                config.specification.suite["protocol"], sort_keys=True
            ),
        }

    def test_every_variant_isolates_one_change_from_the_baseline(self):
        from run_convergence_sweep import COMMON, VARIANTS

        by_name = {v["name"]: v for v in VARIANTS}
        baseline = self.signature(self.resolve(by_name["baseline"], COMMON))
        # The baseline is the shipped preset under the setup's own tolerances.
        self.assertEqual(baseline["tolerance"], (0.005, 1))
        self.assertEqual(baseline["damping"], ())
        expected = {
            "control_tight_tolerance": "tolerance",
            "contact_damping": "damping",
            "gel_mixed_up": "gel_formulation",
            "compact_specimen": "specimen",
        }
        # The attribution set is the controlled comparison. Force-matched runs
        # deliberately carry their own protocol and are checked separately.
        attribution = set(expected) | {"baseline", "combined"}
        signatures = {
            name: self.signature(self.resolve(by_name[name], COMMON))
            for name in attribution
        }
        for name, key in expected.items():
            differing = {k for k in baseline if baseline[k] != signatures[name][k]}
            self.assertEqual(differing, {key}, f"{name} changed {differing}")
        # Material, contact law and loading protocol are fixed across that set.
        for name, other in signatures.items():
            for held in ("material", "contact", "protocol"):
                self.assertEqual(other[held], baseline[held], f"{name}/{held}")
        # The combined run must be exactly the union of the corrections, with the
        # tolerance left at the baseline value rather than tightened again.
        combined = signatures["combined"]
        self.assertEqual(combined["tolerance"], baseline["tolerance"])
        for name, key in expected.items():
            if name == "control_tight_tolerance":
                continue
            self.assertEqual(combined[key], signatures[name][key], f"combined/{key}")
        self.assertEqual(
            {k for k in baseline if baseline[k] != combined[k]},
            {"damping", "gel_formulation", "specimen"},
        )

    def test_force_matched_variant_targets_a_load_not_a_depth(self):
        from run_convergence_sweep import VARIANTS

        variant = next(v for v in VARIANTS if v["name"] == "full_cycle_5n")
        config = Config.load(ROOT / variant["config"])
        protocol = config.specification.suite["protocol"]
        # The travel exists to hit a measured load, so the target and where the
        # number came from have to travel with the config.
        self.assertEqual(protocol["normal_force_target_n"], 5.0)
        self.assertIn("5.0 N", protocol["travel_calibration"])
        self.assertTrue(protocol["release"])
        self.assertTrue(protocol["allow_recorded_lift_off"])
        names = [p["name"] for p in protocol["phases"]]
        self.assertEqual(names[0], "press")
        self.assertEqual(names[-1], "release")
        self.assertIn("slide_at_fixed_compression", names)
        # Release must clear first touch and hold the slid position.
        last = protocol["keyframes"][-1]
        self.assertLess(last["normal_travel_m"], 0)
        self.assertEqual(last["x_m"], 0.010)
        # The variant runs the whole protocol; it must not carry a press-only stop.
        self.assertNotIn("--stop-after-s", variant["common"])

    def test_sweep_stops_at_the_end_of_the_press_phase(self):
        from run_convergence_sweep import COMMON

        common = dict(zip(COMMON[::2], COMMON[1::2]))
        config = Config.load(RUBBER)
        press = [
            p
            for p in config.specification.suite["protocol"]["phases"]
            if p["name"] == "press"
        ]
        self.assertEqual(float(common["--stop-after-s"]), press[0]["end_time_s"])
        # Saved frames must land on whole mechanical checkpoints.
        ratio = float(common["--sample-interval-s"]) / float(common["--solve-interval-s"])
        self.assertEqual(ratio, round(ratio))


class NormalControlTests(unittest.TestCase):
    """Travel control and load control, and the segments that cannot be either."""

    FORCE = ROOT / "configs/material_plane_slide/soft_rubber_force_5n.json"
    TRAVEL = ROOT / "configs/material_plane_slide/soft_rubber_5n.json"

    def model_for(self, config):
        import tempfile as tmpmod
        from unittest.mock import MagicMock

        with tmpmod.TemporaryDirectory() as tmp:
            model = AnsysPlane(config, Path(tmp))
            model.mapdl = MagicMock()
            model.mapdl.get_value.return_value = 0
            try:
                model.build()
            except RuntimeError:
                pass
            return model

    def solve_block(self, config, at, previous=None):
        """The real per-substep platen commands, without a licensed solver."""
        model = self.model_for(config)
        pose = config.physical_pose(at)
        if previous is not None:
            model.previous_solver_time = previous + model.time_offset
        return model, pose, model.platen_commands(pose)

    def test_preload_and_release_stay_travel_driven_under_load_control(self):
        case = Config.load(self.FORCE).specification
        self.assertEqual(case.normal_control, "prescribed_normal_force")
        # Before first touch the normal stiffness is zero, so a commanded load has
        # no equilibrium position.
        self.assertFalse(case.force_controlled(-1.0))
        # The handover instant itself belongs to the preload's own load step.
        self.assertFalse(case.force_controlled(0.0))
        for at in (0.02, 2.0, 4.0, 6.0):
            self.assertTrue(case.force_controlled(at), at)
        # Once the load reaches zero, holding it cannot lift clear of the surface.
        self.assertFalse(case.force_controlled(6.8))
        self.assertFalse(case.force_controlled(8.0))

    def test_the_preload_load_step_is_solved_as_travel_not_load(self):
        """The preload is one ramp ending at the handover instant.

        Its load step is driven by the pose at that instant, so if the handover
        counted as load-driven the whole preload would command a force from open
        contact, where there is no equilibrium position to find.
        """
        config = Config.load(self.FORCE)
        case = config.specification
        handover = case.suite["protocol"]["initialization"]["end_time_s"]
        pose = config.physical_pose(handover)
        self.assertFalse(pose.force_controlled)
        self.assertEqual(
            pose.depth_m,
            case.suite["protocol"]["initialization"]["end_normal_travel_m"],
        )
        model, _, commands = self.solve_block(config, handover)
        self.assertTrue(any(c.startswith(f"D,{model.load_node},UZ,") for c in commands))
        self.assertFalse(any(c.startswith("F,") for c in commands))
        # Control changes hands at the first solved instant after it.
        dataset = case.suite["dataset"]
        step = dataset.get("solve_interval_s", dataset["sample_interval_s"])
        self.assertTrue(config.physical_pose(handover + step).force_controlled)

    def test_load_control_frees_the_platen_in_z_and_couples_it(self):
        config = Config.load(self.FORCE)
        lines = deck(config)
        self.assertIn("CP,NEXT,UZ,ALL", lines)
        self.assertIn("D,ALL,UX,0", lines)
        self.assertIn("D,ALL,UY,0", lines)
        # Travel control clamps all three instead.
        self.assertNotIn("CP,NEXT,UZ,ALL", deck(Config.load(self.TRAVEL)))
        # The gel backing stays fully fixed in both modes.
        for path in (self.FORCE, self.TRAVEL):
            self.assertIn("D,ALL,ALL,0", deck(Config.load(path)))

    def test_commanded_load_pushes_toward_the_sensor(self):
        config = Config.load(self.FORCE)
        model, pose, commands = self.solve_block(config, 2.0, previous=1.98)
        self.assertEqual(pose.normal_force_n, 5.0)
        # Travel is positive toward the sensor, so the platen load is along -z.
        self.assertEqual(
            commands,
            [
                "CMSEL,S,GRIP",
                "D,ALL,UX,0",
                "D,ALL,UY,0",
                f"DDELE,{model.load_node},UZ",
                f"F,{model.load_node},FZ,-5",
                "KBC,0",
            ],
        )

    def test_the_handover_releases_the_preload_constraint_and_steps_the_load(self):
        """Both halves of the mode change, in one sequence.

        A stale displacement constraint pins the platen while the commanded load
        turns into reaction: the solve converges and delivers the preload force
        instead of the commanded one. And a load that did not exist in the
        previous step ramps from zero, walking the platen out to separation and
        back inside one step.
        """
        config = Config.load(self.FORCE)
        case = config.specification
        handover = case.suite["protocol"]["initialization"]["end_time_s"]
        dataset = case.suite["dataset"]
        step = dataset.get("solve_interval_s", dataset["sample_interval_s"])
        model, pose, commands = self.solve_block(
            config, handover + step, previous=handover
        )
        self.assertTrue(pose.force_controlled)
        self.assertIn(f"DDELE,{model.load_node},UZ", commands)
        self.assertTrue(any(c.startswith(f"F,{model.load_node},FZ,") for c in commands))
        self.assertEqual(commands[-1], "KBC,1")
        self.assertTrue(model.hands_over(pose))
        # Only the handover steps the load; later ones ramp.
        _, later, ramped = self.solve_block(
            config, handover + 2 * step, previous=handover + step
        )
        self.assertEqual(ramped[-1], "KBC,0")

    def test_release_drops_the_load_before_prescribing_the_lift(self):
        config = Config.load(self.FORCE)
        model, pose, commands = self.solve_block(config, 8.0, previous=7.98)
        self.assertFalse(pose.force_controlled)
        self.assertLess(pose.depth_m, 0)
        # The load must go before the position is driven, or the two fight.
        drop = commands.index(f"FDELE,{model.load_node},FZ")
        hold = next(
            i for i, c in enumerate(commands) if c.startswith(f"D,{model.load_node},UZ,")
        )
        self.assertLess(drop, hold)
        self.assertNotIn(f"DDELE,{model.load_node},UZ", commands)

    def test_travel_control_is_unchanged_by_the_new_mode(self):
        config = Config.load(self.TRAVEL)
        self.assertEqual(config.specification.normal_control, "prescribed_platen_travel")
        for at in (-1.0, 0.0, 2.0, 8.0):
            pose = config.physical_pose(at)
            self.assertFalse(pose.force_controlled)
            self.assertIsNone(pose.normal_force_n)

    def test_the_platen_books_balance_when_its_load_is_applied(self):
        """Under load control the platen's share is applied, not held.

        Its normal degree of freedom is coupled and loaded, so ANSYS reports no
        reaction there. Leaving the books as reactions makes the specimen look
        unbalanced by the whole contact force, which is what stopped the first
        load-controlled run one tenth of a second into the press.
        """
        import tempfile as tmpmod
        from unittest.mock import MagicMock

        from gelsight_ansys.metrics import validate_frame

        config = Config.load(self.FORCE)
        with tmpmod.TemporaryDirectory() as tmp:
            model = AnsysPlane(config, Path(tmp))
            model.mapdl = MagicMock()
            model.mapdl.get_value.return_value = 0
            try:
                model.build()
            except RuntimeError:
                pass
            reaction = np.array([1e-12, -2e-12, 0.0])  # no z reaction to report
            moment = np.zeros(3)
            pressed = config.physical_pose(2.0)
            closed, partial = model.platen_load(pressed, reaction, moment)
            self.assertAlmostEqual(closed[2], -pressed.normal_force_n)
            self.assertEqual(list(closed[:2]), list(reaction[:2]))
            # No sentinel: frame_metrics rejects any nonfinite solver array, so
            # the measurable part of the moment is reported and simply not
            # compared against the contact moment.
            self.assertTrue(np.isfinite(partial).all())
            np.testing.assert_array_equal(partial, moment)
            # Travel control keeps the reactions exactly as reported.
            held = config.physical_pose(8.0)
            same, intact = model.platen_load(held, reaction, moment)
            np.testing.assert_array_equal(same, reaction)
            np.testing.assert_array_equal(intact, moment)

        # The delivered load then balances the contact force the gel receives.
        force = np.array([0.0, 0.0, -pressed.normal_force_n])
        metrics = {
            "force_on_gel_n": force.tolist(),
            "force_balance_error_n": 0.0,
            "pilot_force_error_n": float(np.linalg.norm(force - closed)),
            "raster_force_error_n": 0.0,
            "platen_control": "load",
            "pilot_moment_error_nm": None,
        }
        # Only the synthetic in-plane reactions remain; z closes exactly.
        self.assertLess(metrics["pilot_force_error_n"], 1e-11)
        validate_frame(metrics, config)
        # An undelivered load must still be caught.
        metrics["pilot_force_error_n"] = 1.0
        with self.assertRaisesRegex(RuntimeError, "pilot forces"):
            validate_frame(metrics, config)

    def test_the_whole_metric_path_accepts_a_load_controlled_frame(self):
        """Run frame_metrics offline on a load-controlled state.

        Both load-controlled attempts died inside this path after a 20 minute
        preload - first on the platen force books, then on a nonfinite sentinel -
        so it is worth exercising without a solver.
        """
        import tempfile as tmpmod
        from unittest.mock import MagicMock

        from gelsight_ansys.metrics import frame_metrics, validate_frame

        config = Config.load(self.FORCE)
        with tmpmod.TemporaryDirectory() as tmp:
            model = AnsysPlane(config, Path(tmp))
            model.mapdl = MagicMock()
            model.mapdl.get_value.return_value = 0
            try:
                model.build()
            except RuntimeError:
                pass
            model.initial_pilot = np.array([0.0, 0.0, 0.003])
            state = model.reference_state()

        pose = config.physical_pose(2.0)
        self.assertTrue(pose.force_controlled)
        # A uniform downward contact load that matches the command.
        faces = len(state.contact_force_n)
        state.contact_force_n = np.zeros((faces, 3))
        state.contact_force_n[:, 2] = -pose.normal_force_n / faces
        force = state.contact_force_n.sum(axis=0)
        state.backing_reaction_n = -force
        state.pilot_reaction_n, state.pilot_moment_nm = model.platen_load(
            pose, np.zeros(3), np.zeros(3)
        )
        fields = {
            "fov_force_n": force.copy(),
            "raster_force_error_n": np.zeros(3),
        }
        markers = state.reference_m[:4, :2]
        metrics = frame_metrics(state, fields, markers, markers, pose, {"active": False})
        self.assertEqual(metrics["platen_control"], "load")
        self.assertAlmostEqual(metrics["commanded_normal_force_n"], pose.normal_force_n)
        self.assertAlmostEqual(metrics["normal_force_n"], pose.normal_force_n)
        self.assertIsNone(metrics["pilot_moment_error_nm"])
        self.assertLess(metrics["pilot_force_error_n"], 1e-12)
        validate_frame(metrics, config)
        # Every recorded value must survive a summary write.
        self.assertNotIn("NaN", json.dumps(metrics))

    def test_load_controlled_protocols_are_rejected_when_incoherent(self):
        from gelsight_ansys.plane_config import PlaneCase

        source = Config.load(self.FORCE).specification

        def build(mutate):
            case = PlaneCase(source.suite, source.case)
            mutate(case.suite["protocol"])
            return case

        def travel_key(protocol):
            protocol["keyframes"][0]["normal_travel_m"] = 0.0001

        def negative(protocol):
            protocol["keyframes"][2]["normal_force_n"] = -1.0

        def no_contact(protocol):
            protocol["keyframes"][0]["normal_force_n"] = 0.0

        def no_lift(protocol):
            for k in protocol["keyframes"]:
                k.pop("release_travel_m", None)

        for mutate, message in (
            (travel_key, "carry normal_force_n"),
            (negative, "nonnegative normal_force_n"),
            (no_contact, "established contact"),
            (no_lift, "release_travel_m"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                build(mutate).validate()


class TransientWindowTests(unittest.TestCase):
    """Inertia switched on only where the physics needs it."""

    STICK_SLIP = ROOT / "configs/material_plane_slide/soft_rubber_stick_slip_5n.json"
    SHORT = ROOT / "configs/material_plane_slide/rigid_short_slide_5n.json"

    def test_solve_interval_keeps_checkpoints_coarser_than_increments(self):
        """One SOLVE per increment would be a gRPC round trip per 0.1 ms."""
        case = Config.load(self.SHORT).specification
        window = next(w for w in case.transient_windows if w["inertia"])
        inside = [
            t
            for t in case.solve_times
            if window["start_time_s"] < t < window["end_time_s"]
        ]
        span = window["end_time_s"] - window["start_time_s"]
        self.assertLess(len(inside), span / window["time_increment_s"] / 10)
        self.assertAlmostEqual(inside[1] - inside[0], window["solve_interval_s"], 9)
        # The increment itself stays fine, so DELTIM subdivides within a SOLVE.
        self.assertEqual(
            case.time_increment_at(sum(inside[:2]) / 2, 0.01),
            window["time_increment_s"],
        )

    def test_bin_activity_region_gates_the_camera_view_only_when_declared(self):
        """A sliding setup may scope the requirement; it may not hide the rest."""
        from gelsight_ansys.plane_coverage import ContactCoverage

        case = Config.load(self.SHORT).specification
        camera = case.suite["sensor"]["camera"]
        gel = case.suite["sensor"]["gel"]
        reference = np.array(
            [
                [x, y, 0.0]
                for x in (-gel["width_m"] / 2, gel["width_m"] / 2)
                for y in (-gel["length_m"] / 2, gel["length_m"] / 2)
            ]
        )
        quads = np.array([[0, 1, 3, 2]])
        coverage = ContactCoverage(case, reference, quads)
        self.assertIsNotNone(coverage.region)
        # The gate speaks for the imaged region, which is a strict subset.
        self.assertLess(coverage.region.mean(), 1.0)
        self.assertGreater(coverage.region.sum(), 0)
        spans = [
            (e[:-1] + e[1:])[m].ptp() / 2
            for e, m in zip(
                coverage.edges, (coverage.region.any(axis=0), coverage.region.any(axis=1))
            )
        ]
        self.assertLessEqual(spans[0], camera["fov_width_m"])
        self.assertLessEqual(spans[1], camera["fov_height_m"])
        # Undeclared means unchanged: the whole surface is gated, as before.
        case.suite["contact_acceptance"]["macroscopic_contact_bins"].pop("region")
        self.assertIsNone(ContactCoverage(case, reference, quads).region)

    def test_enough_restart_state_is_kept_to_reach_the_last_saved_frame(self):
        """Two restart points made every resume impossible; the gap decides."""
        import numpy as np

        config = Config.load(self.SHORT).with_plane_sampling(
            sample_interval_s=0.1, solve_interval_s=0.02, maximum_time_increment_s=0.02
        )
        case = config.specification
        gap = int(
            np.diff(
                np.searchsorted(case.solve_times, case.frame_times, side="left")
            ).max()
        )
        self.assertGreater(gap, 2)
        line = next(c for c in deck(config) if c.startswith("RESCONTROL,DEFINE"))
        kept = int(line.rsplit(",", 1)[1])
        # A failure lands some way past the last frame, so covering the gap once
        # is not enough to restart from it.
        self.assertGreater(kept, gap)

    def test_time_stepping_accuracy_controls_reach_the_solver(self):
        """Both default to ANSYS's own behaviour and are opt-in per setup."""
        from gelsight_ansys.config import Solver

        default = solution_control_commands(Solver())
        self.assertFalse([c for c in default if c.startswith("CUTCONTROL")])
        tuned = solution_control_commands(
            Solver(transient_points_per_cycle=3, predict_cutback=False)
        )
        self.assertIn("CUTCONTROL,NPOINT,3", tuned)
        self.assertIn("CUTCONTROL,NOITERPREDICT,1", tuned)

    def test_every_frame_must_land_on_a_checkpoint(self):
        """A frame between checkpoints is never written, and never complained of."""
        case = Config.load(self.SHORT).specification
        case.validate_sampling()
        window = next(w for w in case.transient_windows if w["inertia"])
        span = window["end_time_s"] - window["start_time_s"]
        # 0.25 / 0.02 is 12.5 intervals, so refined() rounds to 13 and samples
        # every 0.019231 s - times no 0.005 s checkpoint grid contains.
        window["sample_interval_s"] = 0.02
        self.assertNotAlmostEqual(span / 0.02 % 1, 0.0)
        with self.assertRaisesRegex(ValueError, "mechanical checkpoints"):
            case.validate_sampling()

    def test_a_window_may_not_solve_more_finely_than_it_steps(self):
        case = Config.load(self.SHORT).specification
        window = next(w for w in case.transient_windows if w["inertia"])
        window["solve_interval_s"] = window["time_increment_s"] / 2
        with self.assertRaises(ValueError):
            case.validate_transient()

    def test_mass_reaches_the_solver_for_both_bodies(self):
        config = Config.load(self.STICK_SLIP)
        lines = deck(config)
        # Without MP,DENS a transient analysis integrates an inertia-free model,
        # which is exactly the thing it exists to avoid.
        self.assertIn(f"MP,DENS,1,{config.material.density_kg_m3:.16g}", lines)
        self.assertIn(
            f"MP,DENS,4,{config.specification.bulk['density_kg_m3']:.16g}", lines
        )
        self.assertIn("ANTYPE,TRANS", lines)
        self.assertIn("TRNOPT,FULL", lines)
        # A quasi-static setup keeps the static analysis. Density is still
        # emitted where a material declares it - it is inert without TIMINT - but
        # the shared suite's gel declares none, so the gel carries no mass there.
        static = deck(Config.load(ROOT / "configs/material_plane_slide/soft_rubber.json"))
        self.assertIn("ANTYPE,STATIC", static)
        self.assertNotIn("TRNOPT,FULL", static)
        self.assertFalse([line for line in static if line.startswith("MP,DENS,1,")])

    def test_inertia_is_on_inside_the_window_and_off_outside(self):
        config = Config.load(self.STICK_SLIP)
        case = config.specification
        window = case.transient_windows[0]
        model = self.model(config)
        for at in (0.5, 2.5, window["end_time_s"] + 0.05, 7.0):
            self.assertIsNone(case.transient_at(at), at)
            self.assertEqual(
                model.time_integration_commands(config.physical_pose(at)),
                ["TIMINT,OFF"],
            )
        for at in (window["start_time_s"], 3.0, window["end_time_s"]):
            self.assertIsNotNone(case.transient_at(at), at)
            commands = model.time_integration_commands(config.physical_pose(at))
            self.assertEqual(commands[0], "TIMINT,ON")
            self.assertTrue(commands[1].startswith("TINTP,"))
        # The restart rebuilds from gel.rdb, so the state has to be restated.
        source = (ROOT / "src/gelsight_ansys/plane_mechanics.py").read_text()
        restore = source[
            source.index("def restore_model") : source.index("def solve_interval")
        ]
        self.assertIn("time_integration_commands", restore)

    def model(self, config):
        import tempfile as tmpmod
        from unittest.mock import MagicMock

        with tmpmod.TemporaryDirectory() as tmp:
            model = AnsysPlane(config, Path(tmp))
            model.mapdl = MagicMock()
            model.mapdl.get_value.return_value = 0
            try:
                model.build()
            except RuntimeError:
                pass
            return model

    def test_the_window_resolves_the_wave_it_exists_to_capture(self):
        config = Config.load(self.STICK_SLIP)
        case = config.specification
        window = case.transient_windows[0]
        gel = config.material
        shear = gel.young_pa / (2 * (1 + gel.poisson))
        speed = np.sqrt(shear / gel.density_kg_m3)
        transit = config.gel.thickness_m / speed
        # At least a handful of steps per thickness transit, or the release is
        # integrated straight through.
        self.assertGreater(transit / window["time_increment_s"], 5)
        # And long enough for the burst to decay before inertia is switched off.
        self.assertGreater((window["end_time_s"] - window["start_time_s"]) / transit, 20)

    def test_the_schedule_refines_inside_the_window_only(self):
        config = Config.load(self.STICK_SLIP).with_plane_sampling(
            sample_interval_s=0.1, solve_interval_s=0.02, maximum_time_increment_s=0.02
        )
        case = config.specification
        window = case.transient_windows[0]
        solves, frames = case.solve_times, case.frame_times
        inside = (solves >= window["start_time_s"] - 1e-12) & (
            solves <= window["end_time_s"] + 1e-12
        )
        self.assertAlmostEqual(
            float(np.diff(solves[inside]).max()), window["time_increment_s"]
        )
        self.assertAlmostEqual(float(np.diff(solves[~inside]).min()), 0.02)
        # Every saved frame must fall on a solved instant.
        self.assertTrue(np.all(np.isin(np.round(frames, 12), np.round(solves, 12))))
        self.assertEqual(case.time_increment_at(3.0, 0.02), window["time_increment_s"])
        self.assertEqual(case.time_increment_at(1.0, 0.02), 0.02)

    def test_the_slide_accelerates_instead_of_stepping_to_speed(self):
        config = Config.load(self.STICK_SLIP)
        case = config.specification
        speed = [
            p["speed_m_s"]
            for p in case.suite["protocol"]["phases"]
            if p["name"] == "slide_at_fixed_compression"
        ][0]
        # A velocity step is an infinite acceleration once inertia is present.
        onset = case.transient_windows[0]["start_time_s"]
        ramped = (case.pose(onset + 0.02)["x_m"] - case.pose(onset)["x_m"]) / 0.02
        self.assertLess(ramped, speed)
        self.assertGreater(ramped, 0)
        steady = (case.pose(4.5)["x_m"] - case.pose(4.0)["x_m"]) / 0.5
        self.assertAlmostEqual(steady, speed, places=4)

    def test_incoherent_transient_declarations_are_rejected(self):
        from gelsight_ansys.plane_config import PlaneCase

        source = Config.load(self.STICK_SLIP).specification

        def build(mutate):
            case = PlaneCase(source.suite, source.case)
            mutate(case.suite["protocol"]["transient"])
            return case

        cases = (
            (lambda t: t["windows"][0].update(start_time_s=-1.0), "inside the recording"),
            (
                lambda t: t["windows"][0].update(time_increment_s=0),
                "positive time increment",
            ),
            (
                lambda t: t["windows"][0].update(end_time_s=3.00015),
                "whole time increments",
            ),
            (lambda t: t["windows"][0].update(sample_interval_s=1.5e-4), "whole "),
            (lambda t: t.update(numerical_damping=1.5), "numerical_damping"),
            (lambda t: t.update(nope=1), "accepts windows"),
        )
        for mutate, message in cases:
            with self.assertRaisesRegex(ValueError, message):
                build(mutate).validate()


class SymmetricContactTests(unittest.TestCase):
    """Both pairings defined, ANSYS choosing, and a guard on what it chose."""

    SYMMETRIC = ROOT / "configs/material_plane_slide/soft_rubber_symmetric_5n.json"
    ASYMMETRIC = ROOT / "configs/material_plane_slide/soft_rubber_stiff_contact_5n.json"

    def model(self, config):
        import tempfile as tmpmod
        from unittest.mock import MagicMock

        with tmpmod.TemporaryDirectory() as tmp:
            model = AnsysPlane(config, Path(tmp))
            model.mapdl = MagicMock()
            model.mapdl.get_value.return_value = 0
            try:
                model.build()
            except RuntimeError:
                pass
            return model, (Path(tmp) / "plane_model.inp").read_text().splitlines()

    def test_the_reversed_pair_is_numbered_after_what_the_reader_indexes(self):
        """The gel-side block must keep its element numbers.

        ContactResult reads a contiguous range starting at contact_start + 1 and
        rejects any gap, so the second pair has to land beyond every element the
        first pair's reader walks.
        """
        plain, _ = self.model(Config.load(self.ASYMMETRIC))
        model, lines = self.model(Config.load(self.SYMMETRIC))
        self.assertTrue(model.symmetric_contact)
        self.assertEqual(model.contact_start, plain.contact_start)
        numbers = sorted(
            int(line.split(",")[1]) for line in lines if line.startswith("EN,")
        )
        gel_block = range(
            model.contact_start + 1,
            model.contact_start + 1 + len(model.mesh.surface_quads),
        )
        self.assertTrue(set(gel_block) <= set(numbers))
        self.assertGreater(model.second_pair_start, max(gel_block))
        # Nothing was renumbered: the asymmetric deck's elements keep the same
        # numbers here, and the reversed pair is appended past all of them.
        _, plain_lines = self.model(Config.load(self.ASYMMETRIC))
        plain_numbers = sorted(
            int(line.split(",")[1]) for line in plain_lines if line.startswith("EN,")
        )
        self.assertEqual(numbers[: len(plain_numbers)], plain_numbers)
        self.assertEqual(model.second_pair_start, max(plain_numbers) + 1)

    def test_each_pair_gets_its_own_real_set_with_identical_numerics(self):
        config = Config.load(self.SYMMETRIC)
        _, lines = self.model(config)
        reals = [line for line in lines if line.startswith("R,")]
        self.assertEqual(len(reals), 2)
        # Same numbers, different set: a pair is identified by its real set.
        self.assertEqual(reals[0].split(",")[2:], reals[1].split(",")[2:])
        for number in (1, 2):
            self.assertIn(
                f"RMODIF,{number},23,{-config.indenter.elastic_slip_tolerance_m:.16g}",
                lines,
            )
        # KEYOPT(8)=2 is what hands the choice to the solver.
        self.assertIn("KEYOPT,2,8,2", lines)
        self.assertNotIn("KEYOPT,2,8,2", self.model(Config.load(self.ASYMMETRIC))[1])

    def test_damping_lands_in_the_right_real_set(self):
        from gelsight_ansys.plane_mechanics import contact_damping_commands

        damped = Config.load(self.SYMMETRIC).with_contact_damping(
            stabilization_damping_normal=1e-3, stabilization_damping_activation="always"
        )
        self.assertIn("RMODIF,1,31,0.001", contact_damping_commands(damped.indenter, 2))
        self.assertIn(
            "RMODIF,2,31,0.001", contact_damping_commands(damped.indenter, 2, number=2)
        )

    def test_a_pair_that_carries_part_of_the_load_stops_the_run(self):
        config = Config.load(self.SYMMETRIC)
        model, _ = self.model(config)
        model.last_pose = config.physical_pose(2.0)
        self.assertTrue(model.last_pose.force_controlled)
        commanded = model.last_pose.normal_force_n
        # The whole load on the gel-side pair is what the pipeline assumes.
        model.check_active_pair(np.array([0.0, 0.0, -commanded]))
        # Half of it means ANSYS kept both pairs; none means it ran the other one.
        for share, what in ((0.5, "both pairs"), (0.0, "the reversed pair")):
            with self.assertRaisesRegex(RuntimeError, "gel-side contact pair"):
                model.check_active_pair(np.array([0.0, 0.0, -commanded * share]))
        # An asymmetric model has nothing to check.
        plain, _ = self.model(Config.load(self.ASYMMETRIC))
        plain.last_pose = model.last_pose
        plain.check_active_pair(np.zeros(3))

    def test_symmetric_contact_needs_a_deformable_specimen(self):
        from dataclasses import replace

        config = Config.load(self.SYMMETRIC)
        rigid = replace(config, indenter=replace(config.indenter, deformable=False))
        with self.assertRaisesRegex(ValueError, "deformable specimen"):
            rigid.validate()
