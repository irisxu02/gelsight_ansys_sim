"""Constitutive checks independent of the licensed solver."""

import ctypes
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from gelsight_ansys.ansys.contact import contact_properties
from gelsight_ansys.ansys.materials import fabric_properties, foam_commands
from gelsight_ansys.plane_config import PlaneCase
from gelsight_ansys.plane_mesh import depth_axis

ROOT = Path(__file__).resolve().parents[1]


class PlaneMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("gcc")
        if compiler is None:
            raise unittest.SkipTest("Portable C core tests require gcc")
        cls.temp = tempfile.TemporaryDirectory()
        cls.libs = {}
        for name in ("fabric", "contact"):
            target = Path(cls.temp.name) / (name + ".so")
            subprocess.run(
                [
                    compiler,
                    "-shared",
                    "-fPIC",
                    "-O2",
                    "-std=c11",
                    "-D" + name.upper() + "_CORE_ONLY",
                    str(ROOT / "src/gelsight_ansys/native" / (name + ".c")),
                    "-lm",
                    "-o",
                    str(target),
                ],
                check=True,
            )
            cls.libs[name] = ctypes.CDLL(str(target))
        arr = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
        cls.energy = cls.libs["fabric"].gel_fabric_energy
        cls.energy.argtypes = [arr] * 4
        cls.energy.restype = ctypes.c_double
        cls.contact = cls.libs["contact"].gel_contact_response
        cls.contact.argtypes = [arr] * 3 + [ctypes.c_double] * 5 + [arr] * 3
        cls.contact.restype = ctypes.c_int
        cls.oriented = cls.libs["contact"].gel_contact_oriented
        cls.oriented.argtypes = [arr] * 3 + [ctypes.c_double] * 4 + [arr] * 4
        cls.oriented.restype = ctypes.c_int
        cls.fabric_case = PlaneCase.load(
            ROOT / "configs/material_plane_slide/fluffy_fabric.json"
        )
        cls.props = fabric_properties(cls.fabric_case.bulk)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def evaluate(self, C):
        gradient, hessian = np.zeros(6), np.zeros((6, 6))
        value = self.energy(np.asarray(C, dtype=float), self.props, gradient, hessian)
        return value, gradient, hessian

    def test_fabric_nominal_compression_curve_with_free_lateral_stretch(self):
        C0 = self.props[:9].reshape(3, 3)
        for compression, expected in self.fabric_case.bulk[
            "through_thickness_compression"
        ]["strain_stress_pa"]:
            e = np.r_[np.linalg.solve(C0[:2, :2], C0[:2, 2] * compression), -compression]
            _, g, _ = self.evaluate(np.r_[(1 + e) ** 2, 0.0, 0.0, 0.0])
            P = 2 * (1 + e) * g[:3]
            np.testing.assert_allclose(P, [0, 0, -expected], atol=2e-8)

    def test_fabric_energy_stress_and_consistent_tangent(self):
        C = np.array([1.06, 0.98, 0.64, 0.04, -0.03, 0.02])
        _, g, H = self.evaluate(C)
        fd_g, fd_H = np.zeros(6), np.zeros((6, 6))
        h = 1e-6
        for j in range(6):
            delta = np.eye(6)[j] * h
            plus, gp, _ = self.evaluate(C + delta)
            minus, gm, _ = self.evaluate(C - delta)
            fd_g[j] = (plus - minus) / (2 * h)
            fd_H[:, j] = (gp - gm) / (2 * h)
        np.testing.assert_allclose(g, fd_g, rtol=2e-7, atol=1e-5)
        np.testing.assert_allclose(H, fd_H, rtol=2e-7, atol=1e-4)
        np.testing.assert_allclose(H, H.T, atol=1e-10)
        self.assertTrue(np.isnan(self.evaluate([1, 1, 0.08, 0, 0, 0])[0]))

    def response(self, case, gap=0.0, increment=(0.0, 0.0), pressure=10000.0, dt=0.01):
        p = contact_properties(case.case["contact"], case.suite["contact_numerics"])
        stress, plastic, mu = np.zeros(3), np.zeros(2), np.zeros(2)
        status = self.contact(
            p,
            np.zeros(2),
            np.array(increment),
            -gap,
            pressure,
            1e9,
            2e9,
            dt,
            stress,
            plastic,
            mu,
        )
        return status, stress, plastic, mu

    def test_adhesion_area_healing_and_repulsive_superposition(self):
        case = PlaneCase.load(ROOT / "configs/material_plane_slide/sticky_surface.json")
        gaps = np.linspace(0, 20e-6, 101)
        traction = np.array([self.response(case, gap=g, pressure=0)[1][2] for g in gaps])
        self.assertAlmostEqual(
            -np.sum((traction[1:] + traction[:-1]) * np.diff(gaps) / 2), 0.05, places=12
        )
        self.assertEqual(self.response(case, gap=30e-6, pressure=0)[1][2], 0)
        self.assertEqual(self.response(case, gap=0, pressure=0)[1][2], -5000)
        self.assertEqual(self.response(case, gap=-1e-6, pressure=0)[1][2], -4000)
        # Same gap has the same attraction after an open excursion (reversible).
        self.assertEqual(self.response(case, gap=10e-6, pressure=0)[1][2], -2500)

    def test_anisotropic_friction_ellipse_and_sliding_rate(self):
        case = self.fabric_case
        for increment in ((2e-5, 0.0), (0.0, 2e-5), (2e-5, 3e-5)):
            status, stress, plastic, mu = self.response(case, increment=increment)
            self.assertEqual(status, 2)
            self.assertAlmostEqual(np.linalg.norm(stress[:2] / (mu * 10000)), 1, places=9)
            speed = np.linalg.norm(plastic) / 0.01
            expected = np.array([0.45, 0.7]) + np.array([0.15, 0.2]) * np.exp(
                -speed / 0.001
            )
            np.testing.assert_allclose(mu, expected, rtol=1e-9)
            self.assertGreater(np.dot(stress[:2], plastic), 0)
            # Plastic flow follows the normal of the elliptical yield surface.
            normal = stress[:2] / (mu * 10000) ** 2
            self.assertAlmostEqual(
                np.dot(normal, plastic)
                / np.linalg.norm(normal)
                / np.linalg.norm(plastic),
                1,
                places=9,
            )

    def test_friction_is_invariant_to_contact_basis_rotation(self):
        p = contact_properties(
            self.fabric_case.case["contact"], self.fabric_case.suite["contact_numerics"]
        )
        old, inc = np.array([200.0, -300.0]), np.array([2e-5, 3e-5])
        angle = 0.63
        Q = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        expected, actual = np.zeros(3), np.zeros(3)
        vp, vr, mu = np.zeros(2), np.zeros(2), np.zeros(2)
        self.oriented(p, old, inc, 1e-6, 10000.0, 1e9, 0.01, np.eye(2), expected, vp, mu)
        self.oriented(
            p, Q.T @ old, Q.T @ inc, 1e-6, 10000.0, 1e9, 0.01, Q, actual, vr, mu
        )
        np.testing.assert_allclose(Q @ actual[:2], expected[:2], atol=1e-9)
        np.testing.assert_allclose(Q @ vr, vp, atol=1e-14)
        self.assertEqual(actual[2], expected[2])

    def test_history_and_mesh_contract(self):
        # Identify setups by content: a case names the setup it composes with, so
        # a case carrying its own protocol is not held to the shared one.
        paths = sorted((ROOT / "configs/material_plane_slide").glob("*.json"))
        sources = {p: json.loads(p.read_text()) for p in paths}
        shared = [p for p, d in sources.items() if d.get("setup") == "suite.json"]
        self.assertTrue(shared)
        cases = [PlaneCase.load(p) for p in shared]
        for case in cases:
            self.assertEqual(len(case.frame_times), 601)
            self.assertEqual(case.pose(0)["normal_travel_m"], 0.00025)
            self.assertEqual(case.pose(5)["x_m"], 0.01)
        for path, data in sources.items():
            setup = data.get("setup")
            if setup is None or setup == "suite.json":
                continue
            # A case on its own setup still owns a complete, runnable protocol,
            # but chooses its own slide distance and duration: the short-slide
            # diagnostics deliberately stop at 1 mm.
            other = PlaneCase.load(path)
            self.assertGreater(len(other.frame_times), 1)
            end = other.suite["protocol"]["recorded_interval_s"][1]
            slide = max(other.pose(t)["x_m"] for t in other.frame_times)
            self.assertGreater(slide, 0)
            self.assertEqual(other.pose(end)["x_m"], slide)
            self.assertTrue((path.parent / setup).is_file())
        # The dataset presets share one schedule. A case that brings its own is
        # a worked example of a control mode and says so, so nobody mistakes it
        # for one of them.
        self.assertEqual(
            {
                p.name
                for p in sources
                if sources[p].get("setup") not in (None, "suite.json")
            },
            {
                p.name
                for p in sources
                if sources[p].get("status") == "capability_example"
                and sources[p].get("config_kind") == "contact_simulation"
            },
        )
        dz = np.diff(depth_axis(0.004, 0.000125, 0.0005))
        np.testing.assert_allclose(dz[:4], 0.000125)
        self.assertLessEqual(max(dz[1:] / dz[:-1]), 1.3 + 1e-12)
        foam = next(c for c in cases if c.bulk["model"] == "ogden_hyperfoam")
        commands = foam_commands(foam.bulk, 4)
        self.assertIn("TB,HYPER,4,,1,FOAM", commands)
        # Mass reaches the solver only through MP,DENS, and a transient window
        # without it would integrate an inertia-free model.
        self.assertIn(f"MP,DENS,4,{foam.bulk['density_kg_m3']:.16g}", commands)


if __name__ == "__main__":
    unittest.main()
