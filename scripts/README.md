# Command-line entry points

Every file here is a launcher: a docstring, an import of one package module's
`main`, and `raise SystemExit(main())`. The behaviour lives in
`src/gelsight_ansys` - the dataset workflow in `batch/`, the licensed probes,
coupons and benchmarks in `diagnostics/` - where tests import it rather than
shelling out to it. `tests/test_entry_points.py` checks that every launcher
stays thin and that the command behind it still answers `--help`.

## Run and inspect a simulation

| Script | What it does |
|---|---|
| `run_simulation.py` | Run a configuration. The main entry point; also `--dry-run`. |
| `run_examples.py` | Run several configurations one after another, which is what a one-checkout licence allows; reports each one's status and time. |
| `resume_simulation.py` | Continue an interrupted run from saved states and solver restart files; plane runs also support retained checkpoints. |
| `validate_simulation.py` | Launch licensed general-contact integration runs and check their numerical and output contracts. |
| `validate_plane.py` | Run a plane-contact preset as a licensed validation and record whether it passed. |
| `fill_plane_frame.py` | Render a frame a plane run solved but never wrote, from its result file, and rebuild the report. |
| `validate_plane_contact.py` | Launch licensed contact-law coupons with prescribed gaps and exact force checks; requires native libraries. |
| `render_queue.py` | Discover the shipped presets and run them in order; `--resume-from` a previous queue continues a plane preset that was interrupted there. |
| `start_render_queue.ps1` | Windows launcher for it: snapshots the tree, then starts a detached worker that survives the shell. |
| `export_examples.py` | Write the documentation examples from finished runs. |
| `audit_examples.py` | Check every exported preset against its current preset, manifest hashes, and the RGB difference identity; `--run` audits a curated export outside the catalog. |
| `compare_examples.py` | Plot the force and marker metrics of the four sphere examples against each other. |
| `compare_rendering.py` | Build raw-versus-subtracted RGB illustrations from a finished run; optionally replay general-contact optics at another resolution/backend. |
| `render_mesh_views.py` | Draw the deformed finite-element mesh of a finished run, contoured by contact pressure and displacement, at any exaggeration or viewpoint. |

## Read a solve that is not converging

| Script | Reads | Tells you |
|---|---|---|
| `parse_solver_monitor.py` | `<job>.mntr` | Iteration counts and time increments per substep: whether the solve is getting more expensive before it stops. |
| `parse_contact_tracking.py` | `<job>.cnd` | Contact status chattering, sticking and sliding point counts, penetration. Needs `NLDIAG,CONT,ITER`, which `--diagnose` turns on. |
| `contact_health.py` | A run's saved frames | Per-frame contact pressure and penetration fields, and how far the open front has moved. |

[Convergence](../docs/convergence.md) explains what to do with what they report.

## Environment and hardware

| Script | What it does |
|---|---|
| `gelsight_stream.py` | View and record a USB GelSight Mini, optionally beside a finished run; see [live sensor](../docs/live-sensor.md). |
| `analyze_presses.py` | Measure press patches and the marker grid in recordings and simulation runs alike, to match a simulated depth to a hand press. |
| `ansys_smoke_test.py` | Prove a licensed solver is reachable and solves. |
| `ansys_license_probe.py` | Attempt a Windows Mechanical session license checkout and record the result; does not solve a model. |
| `ansys_size_probe.py` | Test one requested node count using a connected spring chain; defaults to 600,000 nodes. |
| `build_plane_adapters.py` | Build Windows ANSYS libraries for fabric elasticity, custom contact, and contact-history output. |
| `validate_plane_adapters.py` | Launch licensed material coupons checking elasticity, compression, shear, and relaxation. |
| `validate_mesh_import.py` | Launch licensed comparisons of generated and imported targets and deformable imported objects. |
| `freeze_mesh_inputs.py` | Pin mesh inputs so a rerun reproduces a result. |
| `probe_plane_contact.py` | Build a plane model and report contact state without a full run. |

## Benchmarks

`benchmark_projection.py`, `benchmark_solvers.py`, `benchmark_contact.py` and
`benchmark_plane_speed.py` measure the CPU and GPU paths.
[Performance](../docs/performance.md) records what they have reported.

## Where each command lives

| Launcher | Implementation |
|---|---|
| `run_simulation.py`, `resume_simulation.py` | `gelsight_ansys.cli` |
| `validate_simulation.py`, `validate_plane.py`, `validate_mesh_import.py` | `gelsight_ansys.batch.validate_*` |
| `render_queue.py`, `export_examples.py`, `audit_examples.py`, `compare_examples.py`, `compare_rendering.py`, `fill_plane_frame.py`, `freeze_mesh_inputs.py` | `gelsight_ansys.batch.*` |
| `ansys_smoke_test.py`, `ansys_license_probe.py`, `ansys_size_probe.py`, `probe_plane_contact.py` | `gelsight_ansys.diagnostics.*` |
| `benchmark_contact.py`, `benchmark_solvers.py`, `benchmark_projection.py`, `benchmark_plane_speed.py` | `gelsight_ansys.diagnostics.*_benchmark` |
| `validate_plane_adapters.py`, `validate_plane_contact.py` | `gelsight_ansys.diagnostics.*_coupons` |
| `parse_solver_monitor.py`, `parse_contact_tracking.py`, `contact_health.py` | `gelsight_ansys.diagnostics.*_report`, `contact_health` |
| `build_plane_adapters.py` | `gelsight_ansys.native_build` |
| `gelsight_stream.py`, `analyze_presses.py` | `gelsight_ansys.sensor.stream`, `gelsight_ansys.sensor.press` |
