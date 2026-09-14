"""Run supported examples sequentially and replace exports only after validation.

Use a fixed code/configuration snapshot for reproducible unattended batches.
The Windows launcher starts an independent worker process.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from gelsight_ansys.artifacts import ImageFiles
from gelsight_ansys.config import Config

from .audit_examples import audit
from .compare_rendering import comparison_panel, save_comparison_gif, saved_rgb
from .export_examples import export_case
from .presets import CASES

ROOT = Path(__file__).resolve().parents[3]


def now():
    return datetime.now(UTC).isoformat()


def atomic_json(path, data):
    """Publish a status file whole, and survive anyone reading it.

    Windows refuses to replace a file another process has open, so a status
    file is exactly the kind of thing that breaks this: it exists to be read
    while the queue runs. A glance from a terminal, an editor, or a progress
    watcher used to raise PermissionError out of the heartbeat and fail the
    job it was reporting on - once at 250 frames of a slide. The replace is
    retried for a few seconds, which outlasts any reader that is not holding
    the file open on purpose.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    deadline = time.monotonic() + 10
    while True:
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def discover(configs, scale):
    jobs, blocked = [], []
    keys = {name: key for key, name in CASES.items()}
    for path in sorted(configs.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        relative = path.relative_to(configs).as_posix()
        if data.get("config_kind") in (
            "proposed_material_plane_suite",
            "material_plane_suite",
            "object_material_case",
        ):
            continue  # Suite metadata; its individual cases are listed below.
        if data.get("status") in ("diagnostic_variant", "capability_example"):
            # A controlled comparison against a preset, or a worked example of a
            # control mode. Neither is one of the curated dataset presets.
            continue
        if data.get("runnable_with_current_cli") is False:
            blocked.append(
                {
                    "name": data["name"],
                    "config": relative,
                    "status": "blocked",
                    "reason": "Configuration specification requires unimplemented solver capabilities.",
                }
            )
            continue
        config = Config.load(path).with_render_scale(scale)
        plane = config.is_plane
        if config.name not in keys:
            blocked.append(
                {
                    "name": config.name,
                    "config": relative,
                    "status": "blocked",
                    "reason": "No integration validation/export contract for this preset.",
                }
            )
            continue
        jobs.append(
            {
                "name": config.name,
                "key": keys[config.name],
                "config": relative,
                "status": "pending",
                "frame_count": len(config.trajectory),
                "resolution": [config.camera.width_px, config.camera.height_px],
                "kind": "plane" if plane else "indenter",
            }
        )
    order = {name: i for i, name in enumerate(CASES.values())}
    jobs.sort(key=lambda job: order[job["name"]])
    return jobs, blocked


def mark_skipped(jobs, names):
    """Record the presets the operator left out of this queue."""
    for job in jobs:
        if job["name"] in names and job.get("status") != "passed":
            job.update(status="skipped", stage=None, reason="Skipped at the operator's request")
            job.pop("error_type", None)


def retire_removed_jobs(state, current_jobs):
    """Preserve history without resuming presets removed from the catalog."""
    current = {job["name"] for job in current_jobs}
    removed = [job for job in state["jobs"] if job["name"] not in current]
    if removed:
        state.setdefault("retired_jobs", []).extend(removed)
        state["jobs"] = [job for job in state["jobs"] if job["name"] in current]


def publish_status(work, destination, state):
    state["updated_utc"] = now()
    atomic_json(work / "queue-status.json", state)
    atomic_json(destination / "queue-status.json", state)


def progress_frames(work, job):
    roots = list((work / "runs" / job["name"]).glob(job["name"] + "_*/summary.json"))
    if roots:
        try:
            summary = json.loads(max(roots, key=lambda p: p.stat().st_mtime).read_text())
            job["completed_frames"] = len(summary["frames"])
        except (OSError, ValueError, KeyError):
            pass  # A solver process may be in the middle of writing its summary.


def is_mapdl_process(name):
    """Whether a process name is an MAPDL solver executable.

    The solver is `ansys<version>` - `ansys252.exe` on Windows, `ansys252` on
    Linux - and PyMAPDL may also start it as plain `ansys`. Ownership is decided
    afterwards by the working directory, so the version is not pinned here.
    """
    return re.fullmatch(r"ansys\d*(\.exe)?", (name or "").lower()) is not None


class SolverWatchdog:
    """Detect a vanished solver during a declared solve, scoped to this job."""

    def __init__(self, output):
        self.output = output.resolve()
        self.absent_since = None

    def check(self, child):
        import psutil

        summaries = list(self.output.glob("*/summary.json"))
        if not summaries:
            return
        try:
            path = max(summaries, key=lambda p: p.stat().st_mtime)
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if state.get("phase") != "solving" or state.get("status") != "running":
            self.absent_since = None
            return
        solver = str((path.parent / "solver").resolve()).lower()
        active = False
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                if not is_mapdl_process(process.info["name"]):
                    continue
                # PyMAPDL launches with relative input/output filenames and sets
                # cwd; the owned solver path need not appear in the command line.
                if any(solver in a.lower() for a in process.info["cmdline"] or []) or (
                    str(Path(process.cwd()).resolve()).lower() == solver
                ):
                    active = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if active:
            self.absent_since = None
        elif self.absent_since is None:
            self.absent_since = time.monotonic()
        elif time.monotonic() - self.absent_since > 120:
            # This is the owned Python validation client, whose native solver is gone.
            child.terminate()
            raise RuntimeError(
                "ANSYS exited during a solve; stopped its orphaned Python client"
            )


def earlier_runs(work, resume_from, name):
    """Where this preset's earlier attempts live, newest queue first.

    A queue relaunch gets its own work directory, so an attempt interrupted by
    a code fix or a stopped worker sits under the previous one. Both are
    offered; the validator decides which, if either, the restart rules accept.
    """
    roots = [work / "runs" / name]
    if resume_from:
        roots.append(Path(resume_from) / "runs" / name)
    return [root for root in roots if root.is_dir() and any(root.glob(f"{name}_*"))]


def stop_owned(child):
    """Stop a validation client and the solver it launched, whatever its state.

    This runs while another exception is on its way out, so it must not raise
    one of its own: psutil reports a process that has already exited with
    NoSuchProcess, raised `from None`, which replaces the failure being
    reported and suppresses it from the traceback. A solver abort was once
    logged as a missing PID that way.
    """
    import psutil

    processes = []
    try:
        owned = psutil.Process(child.pid)
        processes = [owned, *owned.children(recursive=True)]
    except (psutil.Error, OSError):
        processes = processes or []
    for process in processes:
        try:
            process.kill()
        except (psutil.Error, OSError):
            continue
    try:
        psutil.wait_procs(processes, timeout=30)
    except (psutil.Error, OSError):
        pass


def run_validation(
    work, job, scale, executable, heartbeat, libraries=None, resume_from=None
):
    output = work / "runs" / job["name"]
    output.mkdir(parents=True, exist_ok=True)
    validation = output / "validation.json"
    if validation.is_file():
        records = json.loads(validation.read_text())
        if records.get(job["key"], {}).get("status") == "passed":
            return validation
    process_file = output / "process.json"
    watchdog = SolverWatchdog(output)
    if process_file.is_file():
        import psutil

        previous = json.loads(process_file.read_text())
        try:
            child = psutil.Process(previous["pid"])
            created = previous.get(
                "create_time",
                datetime.fromisoformat(previous["started_utc"]).timestamp(),
            )
            # Creation time prevents adopting an unrelated process after PID reuse.
            if abs(child.create_time() - created) < 5:
                while child.is_running():
                    watchdog.check(child)
                    heartbeat()
                    try:
                        child.wait(timeout=10)
                    except psutil.TimeoutExpired:
                        continue
                    break
                if validation.is_file():
                    records = json.loads(validation.read_text())
                    if records.get(job["key"], {}).get("status") == "passed":
                        return validation
                raise RuntimeError(
                    "Previous validation process finished without a passed record"
                )
        except psutil.NoSuchProcess:
            pass
    command = [
        sys.executable,
        "-B",
        "-u",
        str(ROOT / "scripts/validate_simulation.py"),
        "--case",
        job["key"],
        "--output",
        str(output),
        "--render-scale",
        str(scale),
    ]
    if job.get("kind") == "plane":
        command = [
            sys.executable,
            "-B",
            "-u",
            str(ROOT / "scripts/validate_plane.py"),
            "--config",
            str(ROOT / "configs" / job["config"]),
            "--output",
            str(output),
            "--render-scale",
            str(scale),
        ]
        if libraries:
            command.extend(["--libraries", str(libraries)])
        for root in earlier_runs(work, resume_from, job["name"]):
            command.extend(["--resume-from", str(root)])
            break
    if executable:
        command.extend(["--exec-file", str(executable)])
    with (output / "validation.log").open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        import psutil

        atomic_json(
            output / "process.json",
            {
                "pid": child.pid,
                "started_utc": now(),
                "create_time": psutil.Process(child.pid).create_time(),
            },
        )
        try:
            while child.poll() is None:
                watchdog.check(child)
                heartbeat()
                time.sleep(10)
        except BaseException:
            # Whatever ends the supervision - a watchdog, a failed status
            # write, an interrupt - the job it was supervising must not be left
            # solving. An orphan holds the single licence and competes with the
            # preset the queue moves on to.
            stop_owned(child)
            raise
        if child.returncode:
            raise RuntimeError(f"Validation process exited with code {child.returncode}")
    return validation


def replace_export(staged, destination, work):
    """Swap a checked folder, restoring the old export if promotion fails."""
    target = destination / staged.name
    backup = work / "previous_exports" / staged.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        raise FileExistsError("A previous export backup already exists; inspect it first")
    if target.exists():
        # Keep independent notes; generated artifacts are replaced as one dataset.
        manifest = (
            json.loads((target / "manifest.json").read_text())
            if (target / "manifest.json").exists()
            else {"files": {}}
        )
        generated = set(manifest["files"]) | {"manifest.json"}
        for path in target.rglob("*"):
            relative = path.relative_to(target)
            if (
                path.is_file()
                and relative.as_posix() not in generated
                and not (staged / relative).exists()
            ):
                (staged / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, staged / relative)
        target.rename(backup)
    try:
        staged.rename(target)
    except BaseException:
        if backup.exists() and not target.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--render-scale", type=int, default=4)
    parser.add_argument("--exec-file", type=Path)
    parser.add_argument("--libraries", type=Path)
    parser.add_argument(
        "--resume-from",
        type=Path,
        help="A previous queue's work directory; a plane preset interrupted "
        "there is continued rather than solved again, when the restart rules "
        "accept it",
    )
    parser.add_argument(
        "--reuse-passed",
        type=Path,
        help="Reuse current, already-audited exports from an earlier queue",
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="NAME",
        help="Leave this preset out of the queue; repeatable. It is recorded as "
        "skipped, not failed, and nothing is exported for it.",
    )
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args(argv)
    jobs, blocked = discover(ROOT / "configs", args.render_scale)
    unknown = set(args.skip) - {job["name"] for job in jobs}
    if unknown:
        parser.error("Unknown preset(s) to skip: " + ", ".join(sorted(unknown)))
    mark_skipped(jobs, args.skip)
    if args.list_only:
        print(json.dumps({"jobs": jobs, "blocked": blocked}, indent=2))
        return 0
    work, destination = args.work.resolve(), args.examples.resolve()
    work.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    # OS file locking releases automatically if the worker exits unexpectedly.
    with (work / "worker.lock").open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status_file = work / "queue-status.json"
        state = (
            json.loads(status_file.read_text())
            if status_file.exists()
            else {
                "schema_version": 1,
                "created_utc": now(),
                "render_scale": args.render_scale,
                "jobs": jobs,
                "blocked": blocked,
                "execution": "Sequential ANSYS validation and CUDA rendering; fixed code/config snapshot.",
            }
        )
        retire_removed_jobs(state, jobs)
        mark_skipped(state["jobs"], args.skip)
        if state["render_scale"] != args.render_scale:
            raise ValueError(
                "Use a new queue directory when changing rendering resolution"
            )
        if args.reuse_passed and not status_file.exists():
            previous = json.loads(args.reuse_passed.read_text())
            for job in state["jobs"]:
                old = next(
                    (
                        j
                        for j in previous["jobs"]
                        if j["name"] == job["name"] and j["status"] == "passed"
                    ),
                    None,
                )
                if old is not None:
                    checked = audit(
                        destination / job["name"],
                        ROOT / "configs" / job["config"],
                        args.render_scale,
                    )
                    job.update(
                        status="passed",
                        stage="complete",
                        audit=checked,
                        reused_validated_export=True,
                        completed_frames=job["frame_count"],
                    )
        state["status"] = "running"
        atomic_json(work / "process.json", {"pid": os.getpid(), "started_utc": now()})
        publish_status(work, destination, state)
        for job in state["jobs"]:
            if job["status"] in ("passed", "skipped") or (
                job["status"] == "failed" and not args.retry_failed
            ):
                continue
            job.update(status="running", stage="solve_and_validate", started_utc=now())
            job.pop("error_type", None)
            publish_status(work, destination, state)
            print(f"Starting {job['name']} at {job['resolution']}", flush=True)

            def heartbeat(current_job=job):
                progress_frames(work, current_job)
                publish_status(work, destination, state)

            try:
                if shutil.disk_usage(work).free < 30 * 1024**3:
                    raise RuntimeError(
                        "Less than 30 GiB remains for solver scratch and verified export"
                    )
                validation = run_validation(
                    work,
                    job,
                    args.render_scale,
                    args.exec_file,
                    heartbeat,
                    args.libraries,
                    args.resume_from,
                )
                record = json.loads(validation.read_text())[job["key"]]
                source = validation.parent / record["run"]
                summary = json.loads((source / "summary.json").read_text())
                config = Config.load(source / "config.json", validate=False)
                job.update(
                    stage="comparison_render", completed_frames=len(summary["frames"])
                )
                publish_status(work, destination, state)
                import tempfile

                with tempfile.TemporaryDirectory(
                    prefix="comparison-", dir=work
                ) as temporary:
                    paths = []
                    peak = max(
                        range(len(summary["frames"])),
                        key=lambda i: summary["frames"][i]["normal_force_n"],
                    )
                    for i, (metric, (raw, diff)) in enumerate(
                        zip(summary["frames"], saved_rgb(source, len(summary["frames"])))
                    ):
                        panel = comparison_panel(
                            raw,
                            diff,
                            metric,
                            i,
                            len(summary["frames"]),
                            args.render_scale,
                        )
                        path = Path(temporary) / f"frame_{i:04d}.png"
                        panel.save(path)
                        paths.append(path)
                        if i == peak:
                            panel.save(source / "raw_vs_subtracted.png")
                    save_comparison_gif(
                        ImageFiles(paths),
                        source / "raw_vs_subtracted.gif",
                        config.optics.animation_fps,
                    )
                job["stage"] = "export_and_audit"
                publish_status(work, destination, state)
                staging = work / "staging"
                export_case(validation, job["key"], staging)
                checked = audit(
                    staging / job["name"],
                    ROOT / "configs" / job["config"],
                    args.render_scale,
                )
                replace_export(staging / job["name"], destination, work)
                job.update(
                    status="passed",
                    stage="complete",
                    finished_utc=now(),
                    elapsed_s=summary["elapsed_s"],
                    audit=checked,
                    preview=f"{job['name']}/preview.png",
                    gif=f"{job['name']}/process.gif",
                )
                print(f"Completed and replaced {job['name']}", flush=True)
            except Exception as error:
                (work / f"{job['name']}-error.log").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
                job.update(
                    status="failed", error_type=type(error).__name__, finished_utc=now()
                )
                print(
                    f"Failed {job['name']}: {type(error).__name__}; continuing queue",
                    flush=True,
                )
            publish_status(work, destination, state)
        state["status"] = (
            "finished_with_failures"
            if any(j["status"] == "failed" for j in state["jobs"])
            else "finished_with_blocked_configs"
            if blocked
            else "passed"
        )
        state["finished_utc"] = now()
        publish_status(work, destination, state)
        print(state["status"], flush=True)
        return 1 if any(j["status"] == "failed" for j in state["jobs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
