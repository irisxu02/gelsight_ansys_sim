"""Public geometry/material selection, dispatch, and CLI override contracts."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gelsight_ansys.ansys.contact import friction_commands
from gelsight_ansys.ansys.materials import bulk_commands
from gelsight_ansys.cli import main
from gelsight_ansys.config import Config
from gelsight_ansys.pipeline import run
from gelsight_ansys.plane_mesh import slab_mesh
from gelsight_ansys.simulation_config import ConfigurationError

ROOT = Path(__file__).resolve().parents[1]
PLANE = ROOT / "configs/material_plane_slide/soft_rubber.json"
SPHERE = ROOT / "configs/soft_sphere_press.json"


class UnifiedConfigTests(unittest.TestCase):
    def source(self, path=PLANE):
        return json.loads(path.read_text())

    def resolve(self, data, base=PLANE.parent):
        return Config.from_dict(data, base)

    def test_material_overrides_and_geometry_are_independent(self):
        data = self.source()
        data["object"]["material"]["parameters"] = {"young_pa": 75000}
        data["object"]["geometry"]["width_m"] = 0.05
        selected = self.resolve(data)
        self.assertEqual(selected.specification.bulk["young_pa"], 75000)
        self.assertEqual(Config.load(PLANE).specification.bulk["young_pa"], 50000)
        self.assertEqual(selected.specification.suite["specimen"]["width_m"], 0.05)
        self.assertEqual(selected.trajectory, Config.load(PLANE).trajectory)
        mesh = slab_mesh(selected.specification, 0, object_size=0.005)
        self.assertAlmostEqual(
            float(max(mesh.coordinates[:, 0]) - min(mesh.coordinates[:, 0])), 0.05
        )

    def test_one_material_case_can_drive_sphere_and_plane(self):
        sphere = Config.load(SPHERE)
        data = self.source()
        data["object"]["material"] = {"case": "../materials/elastic_silicone.json"}
        data["contact"] = self.source(SPHERE)["contact"]
        data["contact"].pop("numerics")
        plane = self.resolve(data)
        self.assertEqual(
            plane.specification.bulk["young_pa"], sphere.indenter.material.young_pa
        )
        self.assertEqual(
            plane.specification.bulk["model"], sphere.indenter.material.model
        )
        self.assertEqual(
            plane.specification.case["contact"]["friction"]["static_coefficient"], 0.5
        )
        self.assertTrue(
            any("HYPER" in c for c in bulk_commands(plane.specification.bulk, 4))
        )

    def test_unknown_cases_parameters_and_combinations_are_rejected(self):
        bad = self.source()
        bad["object"]["material"]["parameters"] = {"young_pas": 75000}
        with self.assertRaisesRegex(ConfigurationError, "Unknown material parameter"):
            self.resolve(bad)
        bad = self.source(SPHERE)
        bad["object"]["material"] = {"case": "materials/compressible_foam.json"}
        with self.assertRaisesRegex(ConfigurationError, "requires plane"):
            self.resolve(bad, SPHERE.parent)
        bad["object"]["material"] = {"case": "materials/soft_rubber.json"}
        with self.assertRaisesRegex(
            ConfigurationError, "history currently requires plane"
        ):
            self.resolve(bad, SPHERE.parent)
        bad = self.source()
        bad["object"]["geometry"]["shape"] = "arbitrary_mesh"
        with self.assertRaisesRegex(ConfigurationError, "Supported object shapes"):
            self.resolve(bad)
        bad = self.source()
        bad["contact"]["friction"]["model"] = "unknown_friction"
        with self.assertRaisesRegex(ValueError, "Unsupported plane friction"):
            self.resolve(bad)

    def test_parameter_arrays_replace_and_do_not_mutate_material_case(self):
        data = self.source()
        original = Config.load(PLANE).specification.bulk
        terms = [{"fraction": 0.3, "relaxation_time_s": 1.5}]
        data["object"]["material"]["parameters"] = {
            "viscoelasticity": {"shear_terms": terms}
        }
        material = self.resolve(data).specification.bulk
        self.assertEqual(material["viscoelasticity"]["shear_terms"], terms)
        self.assertEqual(
            material["viscoelasticity"]["bulk_terms"],
            original["viscoelasticity"]["bulk_terms"],
        )
        self.assertNotEqual(
            Config.load(PLANE).specification.bulk["viscoelasticity"]["shear_terms"], terms
        )

    def test_resolved_records_need_no_source_material_files(self):
        config = Config.load(PLANE).with_solver(cores=3, gpu=True, require_gpu=True)
        config = config.with_optics(
            render_mode="subtracted", backend="cpu"
        ).with_render_scale(4)
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary) / "config.json"
            p.write_text(json.dumps(config.to_dict()))
            loaded = Config.load(p)
        self.assertEqual(loaded.to_dict(), config.to_dict())
        self.assertEqual(loaded.camera.width_px, 1280)
        self.assertEqual(loaded.optics.render_mode, "subtracted")
        self.assertTrue(loaded.solver.gpu)

    def test_common_command_passes_plane_controls_and_preserves_cuda_default(self):
        completed = (Path("outputs/test"), {"status": "pilot_passed", "frames": []})
        with (
            patch("gelsight_ansys.pipeline.run", return_value=completed) as execute,
            redirect_stdout(io.StringIO()),
        ):
            code = main(
                [
                    "run",
                    "--config",
                    str(PLANE),
                    "--render-scale",
                    "4",
                    "--object-element-size-m",
                    "0.00025",
                    "--stop-after-s",
                    "0",
                    "--subtract-background",
                    "--libraries",
                    "native-libraries",
                ]
            )
        self.assertEqual(code, 0)
        config = execute.call_args.args[0]
        self.assertIs(type(config), Config)
        self.assertTrue(config.is_plane)
        self.assertEqual(config.camera.width_px, 1280)
        self.assertEqual(config.plane_options.object_element_size_m, 0.00025)
        self.assertEqual(config.optics.backend, "cuda")
        self.assertEqual(config.optics.render_mode, "subtracted")
        self.assertEqual(execute.call_args.kwargs["libraries"], Path("native-libraries"))
        self.assertEqual(execute.call_args.kwargs["stop_after_s"], 0)

    def test_numerics_sweep_flags_reach_the_solver_and_the_contact_pair(self):
        completed = (Path("outputs/test"), {"status": "pilot_passed", "frames": []})
        with (
            patch("gelsight_ansys.pipeline.run", return_value=completed) as execute,
            redirect_stdout(io.StringIO()),
        ):
            code = main(
                [
                    "run",
                    "--config",
                    str(PLANE),
                    "--force-tolerance",
                    "0.001",
                    "--force-norm",
                    "2",
                    "--gel-formulation",
                    "mixed_up",
                    "--contact-damping-normal",
                    "0.001",
                    "--contact-damping-activation",
                    "always",
                    "--diagnose",
                ]
            )
        self.assertEqual(code, 0)
        config = execute.call_args.args[0]
        self.assertEqual(config.solver.force_tolerance, 0.001)
        self.assertEqual(config.solver.force_norm, 2)
        self.assertTrue(config.solver.nonlinear_diagnostics)
        self.assertEqual(config.material.formulation, "mixed_up")
        self.assertEqual(config.indenter.stabilization_damping_normal, 0.001)
        self.assertEqual(config.indenter.stabilization_damping_activation, "always")

    def test_defaults_keep_the_setup_tolerances_and_leave_contact_damping_alone(self):
        config = Config.load(PLANE)
        self.assertEqual(config.solver.force_tolerance, 0.005)
        self.assertEqual(config.solver.force_norm, 1)
        self.assertFalse(config.solver.nonlinear_diagnostics)
        self.assertIsNone(config.indenter.stabilization_damping_normal)
        self.assertIsNone(config.indenter.stabilization_damping_tangential)

    def test_contact_damping_requires_plane_geometry(self):
        with redirect_stderr(io.StringIO()) as errors, patch(
            "gelsight_ansys.pipeline.run"
        ) as execute:
            code = main(
                ["run", "--config", str(SPHERE), "--contact-damping-normal", "0.001"]
            )
        self.assertEqual(code, 1)
        execute.assert_not_called()
        self.assertIn("plane geometry", errors.getvalue())

    def test_resume_refuses_silent_convergence_changes(self):
        with redirect_stderr(io.StringIO()) as errors, patch(
            "gelsight_ansys.pipeline.resume_run"
        ) as execute:
            code = main(
                ["resume", "--run", "unused", "--force-tolerance", "0.005"]
            )
        self.assertEqual(code, 1)
        execute.assert_not_called()
        self.assertIn("--diagnostic-numerics", errors.getvalue())

    def test_public_pipeline_dispatches_to_plane_adapter(self):
        config = Config.load(PLANE)
        expected = (Path("output"), {"status": "passed", "frames": []})
        with (
            patch(
                "gelsight_ansys.plane_pipeline.run_plane", return_value=expected
            ) as execute,
            patch("gelsight_ansys.pipeline.AnsysGel") as sphere,
        ):
            self.assertEqual(
                run(
                    config,
                    "output",
                    base_directory=PLANE.parent,
                    libraries="native",
                    stop_after_s=0,
                ),
                expected,
            )
        sphere.assert_not_called()
        self.assertIs(execute.call_args.args[0], config)
        self.assertEqual(execute.call_args.kwargs["base_directory"], PLANE.parent)
        self.assertEqual(execute.call_args.args[3], "native")

    def test_shared_options_work_for_sphere_and_plane(self):
        for path in (SPHERE, PLANE):
            completed = (Path("output"), {"status": "passed", "frames": []})
            with (
                patch("gelsight_ansys.pipeline.run", return_value=completed) as execute,
                redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "run",
                        "--config",
                        str(path),
                        "--cpu",
                        "--render-scale",
                        "2",
                        "--subtract-background",
                    ]
                )
            self.assertEqual(code, 0)
            config = execute.call_args.args[0]
            self.assertEqual(config.camera.width_px, 640)
            self.assertEqual(config.optics.backend, "cpu")
            self.assertFalse(config.solver.gpu)
            self.assertEqual(config.optics.render_mode, "subtracted")

    def test_all_shipped_presets_use_one_command_without_a_solver_checkout(self):
        paths = [
            p
            for p in (ROOT / "configs").rglob("*.json")
            if self.source(p).get("config_kind") == "contact_simulation"
        ]
        self.assertEqual(len(paths), 28)
        with patch("gelsight_ansys.pipeline.run") as execute:
            for path in paths:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["run", "--config", str(path), "--dry-run"]), 0)
            execute.assert_not_called()

    def test_sampling_cli_preserves_loading_and_rejects_wrong_geometry(self):
        source = ROOT / "configs/material_plane_slide/soft_rubber.json"
        with (
            patch(
                "gelsight_ansys.pipeline.run", return_value=(Path("unused"), {"status": "passed", "frames": []})
            ) as execute,
            redirect_stdout(io.StringIO()),
        ):
            code = main(
                [
                    "run",
                    "--config",
                    str(source),
                    "--sample-interval-s",
                    "0.05",
                    "--object-element-size-m",
                    "0.00075",
                ]
            )
        self.assertEqual(code, 0)
        config = execute.call_args.args[0]
        self.assertEqual(len(config.trajectory), 121)
        self.assertEqual(len(config.specification.solve_times), 601)
        self.assertEqual(config.plane_options.object_element_size_m, 0.00075)
        with (
            patch("gelsight_ansys.pipeline.run") as execute,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                main(["run", "--config", str(SPHERE), "--sample-interval-s", "0.05"]), 1
            )
            execute.assert_not_called()

    def test_wrong_geometry_options_do_not_start_ansys(self):
        with (
            patch("gelsight_ansys.pipeline.run") as execute,
            redirect_stderr(io.StringIO()),
        ):
            code = main(["run", "--config", str(SPHERE), "--object-mesh", "matched"])
        self.assertEqual(code, 1)
        execute.assert_not_called()

    def test_frictionless_plane_has_no_division_by_zero(self):
        data = self.source()
        data["contact"]["friction"] = {"model": "coulomb", "coefficient": 0.0}
        config = self.resolve(data)
        commands = friction_commands(
            config.specification.case["contact"],
            config.specification.suite["contact_numerics"],
            3,
        )
        self.assertEqual(commands[0], "MP,MU,3,0")


if __name__ == "__main__":
    unittest.main()
