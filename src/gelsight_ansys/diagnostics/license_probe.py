"""Probe Mechanical 2025 R2 session licensing without changing saved preferences.

Run with the project's Windows Python. A successful checkout is not a solve
or proof of GPU use; run ansys_smoke_test.py separately for those checks.
"""

import argparse
import getpass
import importlib.metadata
import json
import os
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .private import exception_details


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, default=252)
    parser.add_argument("--license", default="Ansys Mechanical Enterprise")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/license",
        help="Parent directory for timestamped run outputs",
    )
    parser.add_argument(
        "--include-private-diagnostics",
        action="store_true",
        help="Include identity, paths, tracebacks, and raw licensing logs",
    )
    parser.add_argument(
        "--fresh-profile",
        action="store_true",
        help="Use a clean temporary profile and reset its license preferences",
    )
    args = parser.parse_args(argv)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output.resolve() / stamp
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "timestamp_utc": stamp,
        "python": platform.python_version(),
        "mechanical_version": args.version,
        "requested_license": args.license,
        "fresh_profile": args.fresh_profile,
        "scope": "Session checkout only; no numerical solve or GPU validation",
        "events": [],
        "checkout_observed": False,
    }

    if args.include_private_diagnostics:
        summary.update(
            hostname=platform.node(),
            username=getpass.getuser(),
            lm_project=os.environ.get("LM_PROJECT"),
            output=str(output),
        )

    def record(event, **details):
        entry = {"event": event, **details}
        summary["events"].append(entry)
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(json.dumps(entry), flush=True)

    app = None
    manager = None
    try:
        summary["pymechanical"] = importlib.metadata.version("ansys-mechanical-core")
        from ansys.mechanical.core import App

        record("starting")
        # Copy the current profile into a temporary profile to avoid GUI conflicts.
        # readonly/start_license constructor arguments require 2026 R1; omit at 252.
        app = App(
            version=args.version,
            private_appdata=True,
            copy_profile=not args.fresh_profile,
        )
        manager = app.license_manager
        record("started", product=str(app), readonly=bool(app.readonly))
        if args.fresh_profile:
            manager.reset_preference()  # Only the disposable profile is changed.
            record("temporary_preferences_reset")
        licenses = list(manager.get_all_licenses())
        record(
            "preferences",
            licenses=[
                {"name": str(name), "status": str(manager.get_license_status(name))}
                for name in licenses
            ],
        )
        # Enabled means selected in preferences, not that the server granted it.
        for selected in (None, args.license):
            manager.disable_session_license()
            record(
                "before_checkout",
                license=selected or "default",
                readonly=bool(app.readonly),
            )
            try:
                manager.enable_session_license(selected)
                granted = not bool(app.readonly)
                summary["checkout_observed"] = summary["checkout_observed"] or granted
                record(
                    "checkout",
                    license=selected or "default",
                    granted=granted,
                    readonly=bool(app.readonly),
                )
            except Exception as error:
                record(
                    "checkout_error",
                    license=selected or "default",
                    **exception_details(error, args.include_private_diagnostics),
                )
            if summary["checkout_observed"]:
                break
    except Exception as error:
        record("error", **exception_details(error, args.include_private_diagnostics))
    finally:
        if manager is not None:
            try:
                manager.disable_session_license()
                record("released", readonly=bool(app.readonly))
            except Exception as error:
                record(
                    "release_error",
                    **exception_details(error, args.include_private_diagnostics),
                )
        if app is not None:
            if args.include_private_diagnostics:
                try:
                    log_source = Path(os.environ["TEMP"]) / ".ansys"
                    if log_source.is_dir():
                        log_target = output / "licensing_logs"
                        log_target.mkdir(exist_ok=True)
                        for source in log_source.iterdir():
                            if source.is_file() and source.suffix in (".log", ".out"):
                                shutil.copy2(source, log_target / source.name)
                        record("logs_saved")
                except Exception as error:
                    record("log_copy_error", **exception_details(error, True))
            try:
                app.exit()
            except Exception as error:
                record(
                    "exit_error",
                    **exception_details(error, args.include_private_diagnostics),
                )
        record("finished", checkout_observed=summary["checkout_observed"])
    return 0 if summary["checkout_observed"] else 1
