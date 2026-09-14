"""Every script is a launcher, and every launcher reaches a working command.

Scripts hold no implementation: each names one module in the package and calls
its main. That keeps the behaviour under test rather than in a file the test
suite never imports, and it makes a moved module a failure here rather than a
traceback the first time somebody runs the command.
"""

import ast
import contextlib
import importlib
import io
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))


def imported_module(path):
    """The package module a launcher imports main from."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "main" for alias in node.names
        ):
            return node.module
    return None


class EntryPointTests(unittest.TestCase):
    def test_every_script_is_a_thin_launcher(self):
        self.assertTrue(SCRIPTS)
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertLessEqual(
                    len(body.splitlines()), 20, "a launcher holds no implementation"
                )
                tree = ast.parse(body)
                defined = [
                    node
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef | ast.ClassDef)
                ]
                self.assertEqual(defined, [], "implementation belongs in the package")
                module = imported_module(path)
                self.assertIsNotNone(module, "a launcher imports main")
                self.assertTrue(module.startswith("gelsight_ansys."))

    def test_every_command_responds_to_help(self):
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                module = importlib.import_module(imported_module(path))
                main = module.main
                with (
                    contextlib.redirect_stdout(io.StringIO()) as out,
                    self.assertRaises(SystemExit) as exit,
                ):
                    # Every command takes its arguments as a list, so a test can
                    # call it without going through the process's own argv.
                    main(["--help"])
                self.assertEqual(exit.exception.code, 0)
                self.assertIn("usage", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
