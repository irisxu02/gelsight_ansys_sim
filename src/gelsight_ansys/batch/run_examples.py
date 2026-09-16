"""Run several example configurations one after another.

An ANSYS allowance commonly permits a single simultaneous solver checkout, so
two examples started at once do not run twice as fast: the second fails to check
out a licence and stops. Working through a list in sequence is therefore the
normal way to run more than one, and it is what this does - the same
`run --config` command the guides give, once per configuration, with a status
line for each and a table at the end.

It is deliberately thin. Anything that decides how an example solves belongs to
that example's configuration, and anything that decides how a curated dataset is
validated and exported belongs to render_queue.
"""

import argparse
import time
from pathlib import Path

from gelsight_ansys.cli import main as run_command


def command_for(path, args):
    """The documented run command for one example."""
    command = [
        "run",
        "--config",
        str(path),
        "--output",
        str(args.output),
        "--render-scale",
        str(args.render_scale),
    ]
    for name, value in (
        ("--exec-file", args.exec_file),
        ("--libraries", args.libraries),
        ("--solver-mode", args.solver_mode),
    ):
        if value is not None:
            command += [name, str(value)]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        action="append",
        required=True,
        metavar="PATH",
        help="An example configuration to run; repeat the flag to queue several",
    )
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--render-scale", type=int, default=1)
    parser.add_argument("--exec-file", type=Path)
    parser.add_argument(
        "--libraries", type=Path, help="Native adapter directory, for fabric and adhesion"
    )
    parser.add_argument("--solver-mode", choices=("cpu", "specified"))
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Carry on with the remaining examples after one fails, instead of stopping",
    )
    args = parser.parse_args(argv)

    results = []
    for index, path in enumerate(args.config, start=1):
        print(f"\n===== {index}/{len(args.config)} {path} =====", flush=True)
        started = time.perf_counter()
        code = run_command(command_for(path, args))
        results.append((path, code, time.perf_counter() - started))
        if code and not args.keep_going:
            print("Stopping; pass --keep-going to run the rest anyway.", flush=True)
            break

    print("\n===== summary =====")
    for path, code, elapsed in results:
        print("  %-7s %6.1f min  %s" % ("ok" if not code else "FAILED", elapsed / 60, path))
    skipped = len(args.config) - len(results)
    if skipped:
        print(f"  {skipped} not started")
    return 0 if not skipped and all(not code for _, code, _ in results) else 1


if __name__ == "__main__":
    main()
