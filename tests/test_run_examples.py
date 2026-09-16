"""Running several examples: one at a time, and what happens when one fails."""

import contextlib
import io
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from gelsight_ansys.batch.run_examples import command_for, main

CONFIGS = ["--config", "a.json", "--config", "b.json", "--config", "c.json"]


def run(argv, codes):
    """Run the queue with a stubbed solver, returning its exit code and calls."""
    calls = []

    def fake(command):
        calls.append(command)
        return codes[len(calls) - 1]

    out = io.StringIO()
    with patch("gelsight_ansys.batch.run_examples.run_command", fake):
        with contextlib.redirect_stdout(out):
            code = main(argv)
    return code, calls, out.getvalue()


class SequentialRunTests(unittest.TestCase):
    def test_examples_run_one_after_another_in_the_order_given(self):
        """A licence that allows one solver checkout makes this the normal way."""
        code, calls, output = run(CONFIGS, [0, 0, 0])
        self.assertEqual(code, 0)
        self.assertEqual([c[2] for c in calls], ["a.json", "b.json", "c.json"])
        self.assertEqual(output.count("ok"), 3)

    def test_a_failure_stops_the_queue_unless_told_otherwise(self):
        code, calls, output = run(CONFIGS, [0, 1, 0])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2, "the third example must not start")
        self.assertIn("FAILED", output)
        self.assertIn("1 not started", output)

        code, calls, output = run([*CONFIGS, "--keep-going"], [0, 1, 0])
        self.assertEqual(code, 1, "a failure is still reported")
        self.assertEqual(len(calls), 3, "the rest ran anyway")
        self.assertNotIn("not started", output)

    def test_each_example_gets_the_documented_run_command(self):
        args = Namespace(
            output=Path("outputs/mine"),
            render_scale=4,
            exec_file=None,
            libraries=None,
            solver_mode=None,
        )
        self.assertEqual(
            command_for(Path("configs/sphere_press.json"), args),
            [
                "run",
                "--config",
                "configs/sphere_press.json",
                "--output",
                "outputs/mine",
                "--render-scale",
                "4",
            ],
        )
        # Optional settings are passed only when asked for, so the command a
        # user reads in the log is the command they could have typed.
        args.libraries = Path("native")
        args.solver_mode = "specified"
        passed = command_for(Path("configs/sphere_press.json"), args)
        self.assertIn("--libraries", passed)
        self.assertIn("--solver-mode", passed)
        self.assertNotIn("--exec-file", passed)


if __name__ == "__main__":
    unittest.main()
