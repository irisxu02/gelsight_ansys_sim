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
from dataclasses import replace
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


def with_release(config, start=6.0, end=8.0):
    """Extend a load-controlled protocol with a travel-driven release.

    Load control cannot lift clear: holding zero load leaves the platen wherever
    the surface last pushed it. The release therefore prescribes travel again,
    and the schema requires release_travel_m on every keyframe from the release
    onward. The shipped comparison stops before this, so the fixture is what a
    setup that wants recovery and pull-off would declare.
    """
    from copy import deepcopy

    from gelsight_ansys.plane_config import PlaneCase

    suite = deepcopy(config.specification.suite)
    protocol = suite["protocol"]
    protocol.update(
        release=True, release_start_time_s=start, allow_recorded_lift_off=True
    )
    protocol["recorded_interval_s"][1] = end
    protocol["phases"].append(
        {"name": "release", "start_time_s": start, "end_time_s": end}
    )
    last = protocol["keyframes"][-1]
    protocol["keyframes"] = [
        k for k in protocol["keyframes"] if k["time_s"] < start
    ] + [
        {**last, "time_s": start, "normal_force_n": 5.0, "release_travel_m": 0.00029},
        {**last, "time_s": end - 0.4, "normal_force_n": 0.0, "release_travel_m": 0.0},
        {**last, "time_s": end, "normal_force_n": 0.0, "release_travel_m": -0.0001},
    ]
    case = PlaneCase(suite, deepcopy(config.specification.case)).validate()
    extended = replace(config, specification=case)
    return replace(
        extended,
        trajectory=tuple(extended.physical_pose(t) for t in case.frame_times),
    ).validate()


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
        # The setup declares damping, and it reaches the solver as declared.
        self.assertEqual(
            contact_damping_commands(config.indenter, 2),
            ["RMODIF,1,31,0.001", "RMODIF,1,32,0.001", "KEYOPT,2,15,3"],
        )
        # Undeclared means no commands at all, not a silent default.
        bare = config.with_contact_damping(
            stabilization_damping_normal=None,
            stabilization_damping_tangential=None,
        )
        self.assertEqual(contact_damping_commands(bare.indenter, 2), [])
        self.assertNotIn("KEYOPT,2,15,", "".join(deck(bare)))
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


if __name__ == "__main__":
    unittest.main()


class NormalControlTests(unittest.TestCase):
    """Travel control and load control, and the segments that cannot be either."""

    FORCE = ROOT / "configs/material_plane_slide/soft_rubber.json"

    @property
    def TRAVEL(self):
        """The same preset switched back to travel control.

        Every shipped preset commands a load, because that is what makes a
        material comparison a comparison. Travel control is still supported, so
        the fixture is the mode a user would write by hand.
        """
        import shutil
        import tempfile as tmpmod

        tmp = tmpmod.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = Path(tmp)
        shutil.copytree(self.FORCE.parent, root / "material_plane_slide")
        shutil.copytree(self.FORCE.parent.parent / "materials", root / "materials")
        case = root / "material_plane_slide" / self.FORCE.name
        setup = case.parent / json.loads(case.read_text())["setup"]
        suite = json.loads(setup.read_text())
        protocol = suite["protocol"]
        protocol["normal_control"] = "prescribed_platen_travel"
        protocol["initialization"]["end_normal_travel_m"] = 0.00025
        for k in protocol["keyframes"]:
            k.pop("normal_force_n", None)
            k["normal_travel_m"] = 0.00025 if k["time_s"] == 0 else 0.001
        setup.write_text(json.dumps(suite))
        return case

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
        case = with_release(Config.load(self.FORCE)).specification
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
        config = with_release(Config.load(self.FORCE))
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
        end = config.specification.suite["protocol"]["recorded_interval_s"][1]
        for at in (-1.0, 0.0, 2.0, end):
            pose = config.physical_pose(at)
            self.assertFalse(pose.force_controlled)
            self.assertIsNone(pose.normal_force_n)

    def test_the_platen_books_balance_when_its_load_is_applied(self):
        """Under load control the platen's share is applied, not held.

        Its normal degree of freedom is coupled and loaded, so ANSYS reports no
        reaction there. Leaving the books as reactions makes the specimen look
        unbalanced by the whole contact force, and the run stops on the balance
        check within a tenth of a second of the handover.
        """
        import tempfile as tmpmod
        from unittest.mock import MagicMock

        from gelsight_ansys.metrics import validate_frame

        config = with_release(Config.load(self.FORCE))
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

        source = with_release(Config.load(self.FORCE)).specification

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

    TRANSIENT = ROOT / "configs/material_plane_slide/soft_rubber.json"

    def test_solve_interval_keeps_checkpoints_coarser_than_increments(self):
        """One SOLVE per increment would be a gRPC round trip per 0.1 ms."""
        case = Config.load(self.TRANSIENT).specification
        window = next(w for w in case.transient_windows if w["inertia"])
        inside = [
            t
            for t in case.solve_times
            if window["start_time_s"] < t < window["end_time_s"]
        ]
        span = window["end_time_s"] - window["start_time_s"]
        # One SOLVE per increment would be a gRPC round trip per increment.
        self.assertGreater(window["solve_interval_s"], window["time_increment_s"])
        self.assertLess(len(inside), round(span / window["time_increment_s"]))
        self.assertAlmostEqual(inside[1] - inside[0], window["solve_interval_s"], 9)
        # The increment itself stays fine, so DELTIM subdivides within a SOLVE.
        self.assertEqual(
            case.time_increment_at(sum(inside[:2]) / 2, 0.01),
            window["time_increment_s"],
        )

    def test_bin_activity_region_gates_the_camera_view_only_when_declared(self):
        """A sliding setup may scope the requirement; it may not hide the rest."""
        from gelsight_ansys.plane_coverage import ContactCoverage

        case = Config.load(self.TRANSIENT).specification
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

        config = Config.load(self.TRANSIENT).with_plane_sampling(
            sample_interval_s=0.1, solve_interval_s=0.02, maximum_time_increment_s=0.02
        )
        case = config.specification
        gap = int(
            np.diff(
                np.searchsorted(case.solve_times, case.frame_times, side="left")
            ).max()
        )
        self.assertGreaterEqual(gap, 1)
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
        case = Config.load(self.TRANSIENT).specification
        case.validate_sampling()
        window = next(w for w in case.transient_windows if w["inertia"])
        # A whole multiple of the increment that is not one of the interval the
        # window is solved at: the frames it asks for fall between checkpoints.
        window["sample_interval_s"] = 4 * window["time_increment_s"]
        self.assertNotAlmostEqual(
            window["sample_interval_s"] / window["solve_interval_s"] % 1, 0.0
        )
        with self.assertRaisesRegex(ValueError, "mechanical checkpoints"):
            case.validate_sampling()

    def test_force_balance_is_inertial_inside_the_window_and_strict_outside(self):
        """Contact minus backing is m*a with mass integrated, not an error."""
        from gelsight_ansys.metrics import validate_frame

        config = Config.load(self.TRANSIENT)
        window = next(w for w in config.specification.transient_windows if w["inertia"])
        inside = (window["start_time_s"] + window["end_time_s"]) / 2
        after = window["end_time_s"] + 0.05
        base = {
            "force_on_gel_n": [0.0, 0.0, -5.0],
            "force_balance_error_n": 0.5,  # ten percent of the load
            "pilot_force_error_n": 0.0,
            "raster_force_error_n": 0.0,
        }
        validate_frame({**base, "time_s": inside}, config)
        with self.assertRaisesRegex(RuntimeError, "balance tolerance"):
            validate_frame({**base, "time_s": after}, config)

    def test_a_window_may_not_solve_more_finely_than_it_steps(self):
        case = Config.load(self.TRANSIENT).specification
        window = next(w for w in case.transient_windows if w["inertia"])
        window["solve_interval_s"] = window["time_increment_s"] / 2
        with self.assertRaises(ValueError):
            case.validate_transient()

    def test_mass_reaches_the_solver_for_both_bodies(self):
        config = Config.load(self.TRANSIENT)
        lines = deck(config)
        # Without MP,DENS a transient analysis integrates an inertia-free model,
        # which is exactly the thing it exists to avoid.
        self.assertIn(f"MP,DENS,1,{config.material.density_kg_m3:.16g}", lines)
        self.assertIn(
            f"MP,DENS,4,{config.specification.bulk['density_kg_m3']:.16g}", lines
        )
        self.assertIn("ANTYPE,TRANS", lines)
        self.assertIn("TRNOPT,FULL", lines)
        # A setup without a window keeps the static analysis. Density is still
        # emitted where a material declares it - it is inert without TIMINT - and
        # the gel carries no mass where it declares none.
        from copy import deepcopy

        from gelsight_ansys.plane_config import PlaneCase

        suite = deepcopy(config.specification.suite)
        suite["protocol"].pop("transient")
        suite["sensor"]["material"].pop("density_kg_m3")
        quiet = replace(
            config,
            specification=PlaneCase(suite, deepcopy(config.specification.case)),
        ).with_gel_material(density_kg_m3=None)
        static = deck(quiet)
        self.assertIn("ANTYPE,STATIC", static)
        self.assertNotIn("TRNOPT,FULL", static)
        self.assertFalse([line for line in static if line.startswith("MP,DENS,1,")])

    def test_inertia_is_on_inside_the_window_and_off_outside(self):
        config = Config.load(self.TRANSIENT)
        case = config.specification
        window = case.transient_windows[0]
        model = self.model(config)
        for at in (0.5, 2.5, window["end_time_s"] + 0.05, 5.5):
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
        config = Config.load(self.TRANSIENT)
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
        config = Config.load(self.TRANSIENT).with_plane_sampling(
            sample_interval_s=0.1, solve_interval_s=0.02, maximum_time_increment_s=0.02
        )
        case = config.specification
        window = case.transient_windows[0]
        solves, frames = case.solve_times, case.frame_times
        inside = (solves >= window["start_time_s"] - 1e-12) & (
            solves <= window["end_time_s"] + 1e-12
        )
        self.assertAlmostEqual(
            float(np.diff(solves[inside]).max()),
            window.get("solve_interval_s", window["time_increment_s"]),
        )
        self.assertAlmostEqual(float(np.diff(solves[~inside]).min()), 0.02)
        # Every saved frame must fall on a solved instant.
        self.assertTrue(np.all(np.isin(np.round(frames, 12), np.round(solves, 12))))
        self.assertEqual(case.time_increment_at(3.0, 0.02), window["time_increment_s"])
        self.assertEqual(case.time_increment_at(1.0, 0.02), 0.02)

    def test_the_slide_accelerates_instead_of_stepping_to_speed(self):
        config = Config.load(self.TRANSIENT)
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

        source = Config.load(self.TRANSIENT).specification

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

    BASE = ROOT / "configs/material_plane_slide/soft_rubber.json"

    def pairings(self, case=None):
        """One shipped preset, resolved with the reversed pair off and on.

        The setup is written out and loaded rather than patched in memory, so the
        pairing travels the path a user's config would: resolution reads
        contact_numerics.symmetric_pair from the setup the case names.
        """
        import shutil
        import tempfile as tmpmod

        case = self.BASE if case is None else case
        built = []
        for symmetric in (False, True):
            tmp = tmpmod.mkdtemp()
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            root = Path(tmp)
            shutil.copytree(case.parent, root / "material_plane_slide")
            shutil.copytree(case.parent.parent / "materials", root / "materials")
            copied = root / "material_plane_slide" / case.name
            setup = copied.parent / json.loads(copied.read_text())["setup"]
            suite = json.loads(setup.read_text())
            suite["contact_numerics"]["symmetric_pair"] = symmetric
            setup.write_text(json.dumps(suite))
            built.append(Config.load(copied))
        assert [c.indenter.symmetric_contact for c in built] == [False, True]
        return built

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
        asymmetric, symmetric = self.pairings()
        plain, _ = self.model(asymmetric)
        model, lines = self.model(symmetric)
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
        _, plain_lines = self.model(asymmetric)
        plain_numbers = sorted(
            int(line.split(",")[1]) for line in plain_lines if line.startswith("EN,")
        )
        self.assertEqual(numbers[: len(plain_numbers)], plain_numbers)
        self.assertEqual(model.second_pair_start, max(plain_numbers) + 1)

    def test_each_pair_gets_its_own_real_set_with_identical_numerics(self):
        config = self.pairings()[1]
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
        self.assertNotIn("KEYOPT,2,8,2", self.model(self.pairings()[0])[1])

    def test_damping_lands_in_the_right_real_set(self):
        from gelsight_ansys.plane_mechanics import contact_damping_commands

        damped = self.pairings()[1].with_contact_damping(
            stabilization_damping_normal=1e-3, stabilization_damping_activation="always"
        )
        self.assertIn("RMODIF,1,31,0.001", contact_damping_commands(damped.indenter, 2))
        self.assertIn(
            "RMODIF,2,31,0.001", contact_damping_commands(damped.indenter, 2, number=2)
        )

    def test_a_pair_that_carries_part_of_the_load_stops_the_run(self):
        # Load control, so the commanded share is a number the guard can check.
        config = self.pairings()[1]
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
        plain, _ = self.model(self.pairings()[0])
        plain.last_pose = model.last_pose
        plain.check_active_pair(np.zeros(3))

    def test_symmetric_contact_needs_a_deformable_specimen(self):
        from dataclasses import replace

        config = self.pairings()[1]
        rigid = replace(config, indenter=replace(config.indenter, deformable=False))
        with self.assertRaisesRegex(ValueError, "deformable specimen"):
            rigid.validate()
