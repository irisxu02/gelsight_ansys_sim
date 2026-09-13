# Contributing

[Project overview](README.md) · [Documentation](docs/README.md)

Keep changes focused and record units and assumptions for numerical changes.
Mechanics, optical rendering, and numerical validation have separate contracts;
see the [architecture guide](docs/architecture.md).

## Environment

Follow [Getting started](docs/getting-started.md) for Linux with pyenv or Windows.
CPU checks need NumPy, Pillow, and Matplotlib; ANSYS and CUDA are not required.

## Local checks

Run from the repository root with the project environment active:

```bash
python -B -m unittest discover -s tests -v
python scripts/ansys_smoke_test.py --help
python scripts/run_simulation.py --help
```

GitHub Actions runs CPU tests, Python syntax checks, and CLI help on Windows
and Linux. Solver changes also require appropriate
[licensed numerical checks](docs/verification.md#run-the-checks). Report the
ANSYS version, CPU/GPU mode, numerical errors, and GPU work evidence. Source
checks alone do not validate a mechanics change.

## Code boundaries

Use `Config` for resolved simulations and keep source-reference resolution in
`simulation_config.py`. Put ANSYS constitutive/contact commands in `ansys/`;
configuration validation must not emit solver commands. Geometry adapters own
meshing, fixtures, stepping, and result extraction. Share process ownership through
`AnsysSession` and export/report handling through `run_services.py`.

Put reusable queue, audit, export, and comparison logic in `gelsight_ansys.batch`.
Keep the curated experiment list in `batch/presets.py` so validation, discovery,
and export agree. Keep command launchers thin and import package modules in tests. A refactor should
preserve effective parameters, mesh connectivity, generated solver commands, and
frame fields. Keep baseline artifacts under ignored `outputs/`; a command-deck
comparison does not establish nonlinear convergence.

## Documentation

The root README is the entry point. Put setup in `docs/getting-started.md`,
commands in `docs/usage.md`, scenario details in `docs/examples/README.md`,
physical assumptions in the modeling/material/camera guides, and array contracts
in `docs/dataset.md`. Keep exploratory benchmark reports, partial or failed-run
results, and experiment logs under ignored `outputs/`. Public docs may contain
curated, validated results with their methods and scope; numerical success alone
does not establish calibration or mesh convergence. Add new guides to the
[documentation index](docs/README.md).

When changing a preset, distinguish it from existing exported snapshots. Check
relative links and command options; include the Linux executable override when
documenting a Linux mechanics launch. Label untested platform instructions and
experimental formulations explicitly.

## Sharing results

Use the [example exporter](docs/examples/README.md#exporting-new-snapshots) for
complete passed runs. Keep generated solver files, virtual environments, license
files, support messages, and local settings out of commits. Review diagnostic
logs for identifying information; use synthetic identities and reserved example
domains in tests and documentation.

Example exports keep their full data locally. Commit only the curated PNG/GIF
previews and Markdown allowed by `.gitignore`. Generated JSON under `docs/`,
raw arrays, frame images, metrics, and local HTML viewers are excluded; source
JSON presets under `configs/` are included. Do not force-add generated datasets.

Preserve attribution and verify reuse rights before importing third-party code,
calibration files, meshes, or other assets.
