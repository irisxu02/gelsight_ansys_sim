"""Run the plane-contact convergence variants from scratch, one at a time.

Each variant is a full solve from preload, because contact real constants, the
element formulation and the mesh are written into gel.rdb when the model is first
built and cannot be changed on a restart. Variants run sequentially: the license
is a shared campus server, so concurrent sessions are not dependable.

Setup mode snapshots the source tree and writes a detached launcher. Execute mode
is what the detached supervisor runs, from inside that snapshot.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PYTHON = r"C:\Users\ruohan\AppData\Local\gelsight-ansys\venv\Scripts\python.exe"
SNAPSHOT_TREES = ("src", "scripts", "configs")

# Shared mechanical schedule, matched to the recorded failure so the variants are
# comparable against it. The press phase ends at 2 s with 1 mm commanded travel.
COMMON = (
    "--maximum-time-increment-s",
    "0.02",
    "--solve-interval-s",
    "0.02",
    "--sample-interval-s",
    "0.1",
    "--stop-after-s",
    "2.0",
    "--render-scale",
    "4",
)
RUBBER = "configs/material_plane_slide/soft_rubber.json"
VARIANTS = [
    {
        "name": "baseline",
        "config": RUBBER,
        "arguments": [],
        "question": "Does the setup's declared 0.005/L1 tolerance carry the full press?",
    },
    {
        "name": "control_tight_tolerance",
        "config": RUBBER,
        "arguments": ["--force-tolerance", "0.0001", "--force-norm", "2", "--diagnose"],
        "question": "Does the 1e-4/L2 criterion still abort, and in which elements?",
        "expect": "failure at about t=1.38 s, reproducing the recorded run",
    },
    {
        "name": "contact_damping",
        "config": RUBBER,
        "arguments": [
            "--contact-damping-normal",
            "1e-3",
            "--contact-damping-activation",
            "always",
        ],
        "question": "Does damping the opening footprint edge steady the solve?",
    },
    {
        "name": "gel_mixed_up",
        "config": RUBBER,
        "arguments": ["--gel-formulation", "mixed_up"],
        "question": "Does a mixed u-P gel change the force curve or the contact edge?",
    },
    {
        "name": "compact_specimen",
        "config": "configs/material_plane_slide/soft_rubber_compact.json",
        "arguments": [],
        "question": "Is the clamped overhang outside the footprint mechanically inert?",
    },
    # Force-matched full cycle: press to the measured 5 N load, slide, release.
    # Travel replaces depth as the control target, so the press no longer runs
    # the stack to 20 N where the last load steps burn most of their iterations.
    {
        "name": "full_cycle_5n",
        "config": "configs/material_plane_slide/soft_rubber_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does the 5 N cycle carry press, slide and release end to end?",
    },
    # Load control plus tangential damping. The travel-driven 5 N cycle cleared
    # press and hold but lost the slide: the whole footprint left stick at once
    # while the coefficient fell 0.60 -> 0.45, which has no static path. Damping
    # the tangential direction is what addresses that; load control is the
    # separate question of whether travel can stop being a per-material constant.
    {
        "name": "force_cycle_5n",
        "config": "configs/material_plane_slide/soft_rubber_force_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does load control plus tangential damping carry the slide?",
    },
    # Both sides of the first slide increment at once. The tangential penalty
    # pair sets how much slip the interface tolerates before a point must change
    # state; the accelerated onset sets how much it is asked for. Stepping from
    # rest to 5 mm/s demanded 100 um in one increment against a 2.5 um allowance,
    # so every point crossed together and the whole interface changed state in a
    # single step. This asks 0.25 um of a 25 um allowance.
    # Raising FKT and SLTO together keeps the interface exactly as stiff - the
    # elastic slip allowance is their ratio, 2.5 um either way - while tripling
    # how far a point may slide before augmentation revisits it. FKT 1 -> 10
    # turned 6 um of slide into 135 um by that mechanism alone. The slide is also
    # cut from 10 mm to 1 mm: every attempt so far has died inside the first half
    # millimetre, and a short dataset that exists beats a long one that does not.
    {
        "name": "rigid_short_slide_5n",
        "config": "configs/material_plane_slide/rigid_short_slide_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does integrating the gel's mass carry the perimeter through slip and separation?",
    },
    {
        "name": "rigid_tangential_ramp_5n",
        "config": "configs/material_plane_slide/rigid_tangential_ramp_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does a gentle first increment against a looser allowance carry the slide?",
    },
    # The tangential half of the contact pair. The rigid control failed on the
    # first slide increment, ruling out the deformable target, its mesh, its
    # compliance and the pairing. Its residual history shows the first slide step
    # converging and being thrown off ten times, jumping up to 326x: augmented
    # Lagrange re-augmenting a converged state. The normal side cannot be the
    # cause at FTOLN = 10 um against 0.69 um of penetration, which leaves FKT and
    # SLTO, untouched all along at 1 and 2.5 um.
    {
        "name": "rigid_tangential_5n",
        "config": "configs/material_plane_slide/rigid_tangential_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does raising FKT and SLTO together stop the tangential re-augmentation?",
    },
    # The control that partitions the hypothesis space. Six runs against the
    # deformable rubber lost the slide between 250 and 400 um with contact status
    # chattering. A smooth rigid target is one exact quad, so a sliding contact
    # point never crosses an internal facet boundary; it also removes the
    # specimen's near incompressibility and the contact pairing question, and it
    # is a quarter of the model. If this slides cleanly the fault is specific to
    # deformable-on-deformable contact; if it does not, the specimen is cleared
    # and the gel side is what to look at.
    {
        "name": "rigid_reference_5n",
        "config": "configs/material_plane_slide/rigid_reference_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does a rigid single-facet target slide where the faceted one could not?",
    },
    # The contact pairing itself. ANSYS asks that the contact surface sit on the
    # softer body with the finer mesh; here that is the specimen on both counts,
    # and it is the target instead. Defining the pair both ways with KEYOPT(8)=2
    # hands the choice to the solver. If it picks the reversed pair the gel-side
    # results the pipeline reads come back empty and the run stops rather than
    # reporting a partial load.
    {
        "name": "symmetric_5n",
        "config": "configs/material_plane_slide/soft_rubber_symmetric_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does letting ANSYS choose the pairing quiet the contact status?",
    },
    # A shorter step across the slide onset. Chattering counts contact status
    # changes per load step, and at 0.02 s the interface slid 100 um per step -
    # a fifth of a target element. Five runs lost the step between 370 and 400 um
    # of slide whatever the friction law, normal control, damping or inertia.
    {
        "name": "fine_slide_5n",
        "config": "configs/material_plane_slide/soft_rubber_fine_slide_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does a 25 um step through the onset keep the contact status quiet?",
    },
    # Penalty and threshold together. Loosening the threshold alone stopped the
    # chattering but let penetration reach 26 um, because augmentation - not the
    # penalty - had been holding it at 0.55 um. Penetration scales as 1/FKN, so
    # a ten times stiffer penalty holds about 2.6 um on its own and the threshold
    # can sit above it without firing every step.
    {
        "name": "stiff_contact_5n",
        "config": "configs/material_plane_slide/soft_rubber_stiff_contact_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does a stiffer penalty stop the chattering without spoiling the surface?",
    },
    # The penetration tolerance, at the ANSYS default. Four runs died after the
    # same 370 um of slide under different normal control, damping, inertia and
    # friction law; ANSYS's own chattering level climbed 0 -> 12 across that
    # distance. A 1 um absolute tolerance cannot survive sliding a contact point
    # over a 0.5 mm faceted target, so augmentation fires every step.
    {
        "name": "default_ftoln_5n",
        "config": "configs/material_plane_slide/soft_rubber_default_ftoln_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does the default penetration tolerance stop the contact chattering?",
    },
    # Constant Coulomb friction. Three runs died at t=3.06-3.08 under different
    # control modes, damping and with an inertia window, all at a global shear
    # ratio near 0.05 - nowhere near the static coefficient. The perimeter always
    # carries zero-pressure points whose friction capacity is mu*p = 0; shear
    # makes them flip between open, sliding and sticking, and dmu/dv < 0 turns
    # each flip into a self-amplifying one. Constant friction removes that.
    {
        "name": "constant_friction_5n",
        "config": "configs/material_plane_slide/soft_rubber_constant_friction_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does constant friction carry the slide the velocity-weakening law could not?",
    },
    # Stick-slip, studied rather than avoided. Every quasi-static attempt lost
    # the slide onset: the interface leaves stick across the whole footprint at
    # once and the coefficient falls 0.60 -> 0.45, a drop with no static
    # equilibrium path. Inertia supplies that path, but only inside a 30 ms
    # window - integrating 8 s at 0.1 ms would take weeks.
    {
        "name": "stick_slip_5n",
        "config": "configs/material_plane_slide/soft_rubber_stick_slip_5n.json",
        "arguments": ["--diagnose"],
        "common": [
            "--maximum-time-increment-s",
            "0.02",
            "--solve-interval-s",
            "0.02",
            "--sample-interval-s",
            "0.1",
            "--render-scale",
            "4",
        ],
        "question": "Does inertia carry the interface through the stick-slip release?",
    },
    # Every correction at once. This answers whether the model runs, not which
    # change made it run, so it carries --diagnose: a failure here is expensive
    # to reproduce and the residual and contact files name the elements involved.
    {
        "name": "combined",
        "config": "configs/material_plane_slide/soft_rubber_compact.json",
        "arguments": [
            "--gel-formulation",
            "mixed_up",
            "--contact-damping-normal",
            "1e-3",
            "--contact-damping-activation",
            "always",
            "--diagnose",
        ],
        "question": "Does the fully corrected model carry the press to 1 mm travel?",
        "attribution": "None. Stacked changes; compare against the single-change variants to attribute.",
    },
]


def utc():
    return datetime.now(UTC).isoformat()


def snapshot(destination):
    """Copy the source tree so later edits cannot change a running variant."""
    manifest = {}
    for tree in SNAPSHOT_TREES:
        for path in sorted((ROOT / tree).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            manifest[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def windows_path(path):
    return subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    ).stdout.strip()


def setup(output, python, variants, launch):
    if output.exists():
        raise SystemExit(f"Sweep directory already exists: {output}")
    output.mkdir(parents=True)
    manifest = snapshot(output / "source")
    (output / "snapshot-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output / "variants.json").write_text(
        json.dumps({"common": list(COMMON), "variants": variants}, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "status.json").write_text(
        json.dumps(
            {
                "status": "created",
                "created_utc": utc(),
                "variants": [v["name"] for v in variants],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    supervisor = windows_path(output / "source/scripts/run_convergence_sweep.py")
    here = windows_path(output)
    script = f"""$ErrorActionPreference = 'Stop'
$python = '{python}'
$command = '"' + $python + '" -B -u "{supervisor}" --execute "{here}"'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
    -Arguments @{{ CommandLine = $command; CurrentDirectory = '{here}' }}
if ($result.ReturnValue -ne 0) {{ throw ('Detached launch failed: ' + $result.ReturnValue) }}
@{{ supervisor_pid = $result.ProcessId; started_utc = (Get-Date).ToUniversalTime().ToString('o') }} |
    ConvertTo-Json | Set-Content -LiteralPath '{here}\\launch.json' -Encoding UTF8
Write-Output ('Detached sweep supervisor PID: ' + $result.ProcessId)
"""
    (output / "launch.ps1").write_text(script, encoding="utf-8")
    print(f"Sweep prepared: {output}")
    print(f"  variants: {', '.join(v['name'] for v in variants)}")
    print(f"  snapshot: {len(manifest)} files")
    if launch:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                windows_path(output / "launch.ps1"),
            ],
            capture_output=True,
            text=True,
        )
        print(result.stdout.strip() or result.stderr.strip())
        return 0 if result.returncode == 0 else 1
    print(f"  launch:   powershell.exe -File {windows_path(output / 'launch.ps1')}")
    return 0


def inspect_run(folder):
    """Latest solver progress for a variant, tolerating partial writes."""
    summaries = list(folder.glob("runs/*/summary.json"))
    if not summaries:
        return {}
    path = max(summaries, key=lambda p: p.stat().st_mtime)
    try:
        summary = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    frames, substeps = summary.get("frames", []), summary.get("recorded_substeps", [])
    record = {
        "run": path.parent.name,
        "phase": summary.get("phase"),
        "run_status": summary.get("status"),
        "recorded_frames": len(frames),
    }
    if frames:
        record["last_saved_time_s"] = frames[-1]["time_s"]
        record["normal_force_n"] = frames[-1].get("normal_force_n")
    if substeps:
        record["last_converged_time_s"] = substeps[-1]["time_s"]
        record["contact_coverage"] = (
            substeps[-1].get("contact_coverage", {}).get("active_bin_fraction")
        )
    if summary.get("initialization_substeps"):
        record["preload_substeps"] = len(summary["initialization_substeps"])
    return record


def execute(output):
    """Run every variant in order, recording each outcome and continuing on failure."""
    from gelsight_ansys.batch.render_queue import SolverWatchdog

    settings = json.loads((output / "variants.json").read_text())
    started = time.perf_counter()
    state = {
        "status": "running",
        "supervisor_pid": os.getpid(),
        "started_utc": utc(),
        "results": [],
    }

    def save(active=None):
        state["elapsed_s"] = time.perf_counter() - started
        state["updated_utc"] = utc()
        if active is not None:
            state["active"] = active
        temporary = output / "status.tmp"
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "status.json")

    save()
    if os.name == "nt":
        import ctypes

        # Keep the machine awake for an unattended overnight sweep.
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    snapshot_root = output / "source"
    for variant in settings["variants"]:
        folder = output / variant["name"]
        folder.mkdir(exist_ok=True)
        command = [
            sys.executable,
            "-B",
            "-u",
            str(snapshot_root / "scripts/run_simulation.py"),
            "run",
            "--config",
            str(snapshot_root / variant["config"]),
            "--output",
            str(folder / "runs"),
            *variant.get("common", settings["common"]),
            *variant["arguments"],
        ]
        (folder / "command.json").write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8"
        )
        result = {
            "name": variant["name"],
            "question": variant["question"],
            "started_utc": utc(),
            "status": "running",
        }
        state["results"].append(result)
        save(active=variant["name"])
        variant_started = time.perf_counter()
        try:
            with (folder / "run.log").open("ab", buffering=0) as log:
                child = subprocess.Popen(
                    command,
                    cwd=snapshot_root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **(
                        {"creationflags": subprocess.CREATE_NO_WINDOW}
                        if os.name == "nt"
                        else {}
                    ),
                )
                result["worker_pid"] = child.pid
                watchdog = SolverWatchdog(folder / "runs")
                while child.poll() is None:
                    result.update(inspect_run(folder))
                    save()
                    watchdog.check(child)
                    time.sleep(15)
                result.update(inspect_run(folder))
                result["exit_code"] = child.returncode
                result["status"] = "passed" if child.returncode == 0 else "failed"
        except BaseException as error:  # A failed variant must not stop the sweep.
            result.update(status="error", error=type(error).__name__)
            (folder / "supervisor-error.log").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            if isinstance(error, KeyboardInterrupt):
                state["status"] = "interrupted"
                save(active=None)
                raise
        result["elapsed_s"] = time.perf_counter() - variant_started
        result["finished_utc"] = utc()
        save()
    state["status"] = "complete"
    save(active=None)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="New sweep directory to prepare (setup mode)"
    )
    parser.add_argument(
        "--execute", type=Path, help="Prepared sweep directory to run (supervisor mode)"
    )
    parser.add_argument("--python", default=WINDOWS_PYTHON, help="Windows interpreter")
    parser.add_argument(
        "--only", action="append", help="Restrict to named variants, repeatable"
    )
    parser.add_argument(
        "--variants-file",
        type=Path,
        help="Run variants from this JSON instead of the built-in list",
    )
    parser.add_argument(
        "--launch", action="store_true", help="Start the detached supervisor now"
    )
    args = parser.parse_args(argv)
    if args.execute:
        sys.path.insert(0, str((args.execute / "source/src").resolve()))
        return execute(args.execute.resolve())
    if not args.output:
        parser.error("Pass --output to prepare a sweep, or --execute to run one")
    chosen = VARIANTS
    if args.variants_file:
        chosen = json.loads(args.variants_file.read_text(encoding="utf-8"))["variants"]
    if args.only:
        names = {v["name"] for v in chosen}
        unknown = set(args.only) - names
        if unknown:
            parser.error(f"Unknown variants: {', '.join(sorted(unknown))}")
        chosen = [v for v in chosen if v["name"] in set(args.only)]
    return setup(args.output.resolve(), args.python, chosen, args.launch)


if __name__ == "__main__":
    raise SystemExit(main())
