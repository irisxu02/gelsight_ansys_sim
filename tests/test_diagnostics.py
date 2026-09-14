"""Check public summaries and opt-in details without an ANSYS installation."""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

smoke = importlib.import_module("gelsight_ansys.diagnostics.smoke_test")
probe = importlib.import_module("gelsight_ansys.diagnostics.license_probe")

PRIVATE_MESSAGE = (
    r"sample_user@SAMPLE-HOST: denied by license-server.example.test; "
    r"profile C:\Users\sample_user"
)


class UnavailableLicense:
    def get_all_licenses(self):
        return ["Ansys Mechanical Enterprise"]

    def get_license_status(self, name):
        return "Enabled"

    def reset_preference(self):
        pass

    def disable_session_license(self):
        pass

    def enable_session_license(self, selected):
        raise RuntimeError(PRIVATE_MESSAGE)


class MechanicalStub:
    readonly = True

    def __init__(self, **kwargs):
        self.license_manager = UnavailableLicense()

    def __str__(self):
        return "Ansys Mechanical []"

    def exit(self):
        pass


class DiagnosticPrivacyTests(unittest.TestCase):
    def check_probe(self, private):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            logs = root / ".ansys"
            logs.mkdir()
            (logs / "licdebug.SAMPLE-HOST.out").write_text(PRIVATE_MESSAGE)
            arguments = ["ansys_license_probe.py", "--output", str(root / "runs")]
            if private:
                arguments.append("--include-private-diagnostics")
            modules = {
                name: ModuleType(name)
                for name in ("ansys", "ansys.mechanical", "ansys.mechanical.core")
            }
            modules["ansys.mechanical.core"].App = MechanicalStub
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch.object(sys, "argv", arguments))
            stack.enter_context(
                patch.object(probe.importlib.metadata, "version", return_value="test")
            )
            stack.enter_context(
                patch.object(probe.getpass, "getuser", return_value="sample_user")
            )
            stack.enter_context(
                patch.object(probe.platform, "node", return_value="SAMPLE-HOST")
            )
            stack.enter_context(
                patch.dict(
                    os.environ, {"TEMP": directory, "LM_PROJECT": "sample_project"}
                )
            )
            console = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(probe.main(), 1)
            summary_path = next((root / "runs").glob("*/summary.json"))
            summary = json.loads(summary_path.read_text())
            self.assertFalse(summary["checkout_observed"])
            self.assertTrue(
                any(event["event"] == "checkout_error" for event in summary["events"])
            )
            self.assertEqual((summary_path.parent / "licensing_logs").exists(), private)
            combined = summary_path.read_text() + console.getvalue()
            for identity in (
                "sample_user",
                "SAMPLE-HOST",
                "license-server.example.test",
                "sample_project",
            ):
                self.assertEqual(identity in combined, private)
            for key in ("hostname", "username", "lm_project", "output"):
                self.assertEqual(key in summary, private)
            self.assertEqual("Traceback" in combined, private)

    def check_smoke(self, private):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = [
                "ansys_smoke_test.py",
                "--exec-file",
                str(root / "sample_user" / "missing.exe"),
                "--output",
                str(root / "runs"),
            ]
            if private:
                arguments.append("--include-private-diagnostics")
            with (
                patch.object(sys, "argv", arguments),
                redirect_stdout(io.StringIO()) as console,
            ):
                self.assertEqual(smoke.main(), 1)
            summary_path = next((root / "runs").glob("*/summary.json"))
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["error_type"], "FileNotFoundError")
            self.assertTrue(summary["gpu_requested"])
            self.assertFalse(summary["gpu_solver_use_verified"])
            for key in ("executable", "ansys_executable", "output"):
                self.assertEqual(key in summary, private)
            combined = summary_path.read_text() + console.getvalue()
            self.assertEqual("sample_user" in combined, private)
            self.assertEqual("Traceback" in combined, private)

    def test_probe_omits_private_details_by_default(self):
        self.check_probe(private=False)

    def test_probe_includes_private_details_only_when_requested(self):
        self.check_probe(private=True)

    def test_smoke_omits_private_details_by_default(self):
        self.check_smoke(private=False)

    def test_smoke_includes_private_details_only_when_requested(self):
        self.check_smoke(private=True)


if __name__ == "__main__":
    unittest.main()
