"""Diagnostic timeouts and licensing failures must not launch more solver work."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_contact import supervise


class ContactBenchmarkTests(unittest.TestCase):
    def args(self, root):
        return SimpleNamespace(
            output=root / "benchmark",
            variants=["baseline", "mixed_up"],
            config=root / "config.json",
            stop_time=-1.7,
            dt=0.01,
            object_size=None,
            exec_file=None,
            timeout_s=0.1,
        )

    def test_timeout_stops_owned_worker_and_preserves_failure_record(self):
        with tempfile.TemporaryDirectory() as temp:
            args = self.args(Path(temp))
            child = Mock(returncode=0)
            child.wait.side_effect = subprocess.TimeoutExpired("diagnostic", 0.1)
            with (
                patch("benchmark_contact.subprocess.Popen", return_value=child),
                patch("benchmark_contact.stop_worker") as stop,
            ):
                supervise(args)
            records = json.loads((args.output / "benchmark.json").read_text())
            self.assertEqual(stop.call_count, 2)
            self.assertTrue(
                all(
                    r["status"] == "failed" and r["error_type"] == "TimeoutError"
                    for r in records.values()
                )
            )
            self.assertEqual(stop.call_args.args[1], args.output / "mixed_up/solver")

    def test_license_error_stops_before_the_next_variant(self):
        with tempfile.TemporaryDirectory() as temp:
            args = self.args(Path(temp))

            def launch(*unused, **kwargs):
                solver = args.output / "baseline/solver"
                solver.mkdir(parents=True)
                (solver / ".__tmp__.out").write_text(
                    "ANSYS LICENSE MANAGER ERROR: test server unavailable"
                )
                return Mock(returncode=1)

            with patch("benchmark_contact.subprocess.Popen", side_effect=launch) as start:
                self.assertEqual(supervise(args), 2)
            self.assertEqual(start.call_count, 1)
            records = json.loads((args.output / "benchmark.json").read_text())
            self.assertEqual(records["baseline"]["status"], "blocked")
            self.assertEqual(records["baseline"]["error_type"], "LicenseUnavailable")
            self.assertNotIn("mixed_up", records)


if __name__ == "__main__":
    unittest.main()
