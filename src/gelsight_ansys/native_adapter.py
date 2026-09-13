"""Portable source/binary provenance checks for process-local ANSYS adapters."""

import hashlib
import json
from pathlib import Path


def verify_libraries(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "build.json").read_text())
    sources = Path(__file__).with_name("native")
    for name in ("contact.c", "fabric.c"):
        if (
            hashlib.sha256((sources / name).read_bytes()).hexdigest()
            != manifest["source_sha256"][name]
        ):
            raise ValueError(
                "Native adapter sources changed; rebuild the libraries before solving"
            )
    for name in ("userinterLib.dll", "userouLib.dll", "UserHyperAnisoLib.dll"):
        if (
            hashlib.sha256((directory / name).read_bytes()).hexdigest()
            != manifest["binary_sha256"][name]
        ):
            raise ValueError("Native adapter binary does not match its build manifest")
    return manifest
