"""Build process-local Windows ANSYS UPF libraries with a MinGW C compiler.

No files are copied into ANSYS. Supply the explicit cross-compiler path; resulting binaries stay under the requested output.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def build(compiler, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = Path(__file__).with_name("native")
    common = [
        str(Path(compiler).resolve()),
        "-O2",
        "-std=c11",
        "-shared",
        "-static-libgcc",
    ]
    subprocess.run(
        common
        + [str(sources / "fabric.c"), "-o", str(output / "UserHyperAnisoLib.dll")],
        check=True,
    )
    subprocess.run(
        common + [str(sources / "contact.c"), "-o", str(output / "userinterLib.dll")],
        check=True,
    )
    shutil.copy2(output / "userinterLib.dll", output / "userouLib.dll")
    manifest = {
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources.glob("*.c")
        },
        "binary_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.glob("*.dll")
        },
        "compiler": subprocess.check_output(
            [str(compiler), "--version"], text=True
        ).splitlines()[0],
        "abi": "Windows x86-64; ANSYS 2025 R2 UPF C calling convention",
        "validated_in_ansys": False,
    }
    (output / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.compiler, args.output), indent=2))
    return 0
