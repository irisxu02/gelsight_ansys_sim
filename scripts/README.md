# Command-line entry points

Every script here is a thin front for a module in `src/gelsight_ansys`, so the
behaviour is tested rather than living in the script. Run any of them with
`--help`.

## Run and inspect a simulation

| Script | What it does |
|---|---|
| `run_simulation.py` | Run a configuration. The main entry point; also `--dry-run`. |
| `resume_simulation.py` | Continue an interrupted run from its last saved frame. |
| `validate_simulation.py` | Check a finished run against its declared contract. |
| `validate_plane.py` | Run a plane-contact preset as a licensed validation and record whether it passed. |
| `fill_plane_frame.py` | Render a frame a plane run solved but never wrote, from its result file, and rebuild the report. |
| `validate_plane_contact.py` | Contact acceptance on a finished plane run. |
| `render_queue.py` | Discover the shipped presets and run them in order. |
| `start_render_queue.ps1` | Windows launcher for it: snapshots the tree, then starts a detached worker that survives the shell. |
| `export_examples.py` | Write the documentation examples from finished runs. |
| `audit_examples.py` | Check every exported preset against its current preset, manifest hashes, and the RGB difference identity; `--run` audits a curated export outside the catalog. |
| `compare_examples.py` | Plot the force and marker metrics of the four sphere examples against each other. |
| `compare_rendering.py` | Compare optical backends on the same mechanical result. |

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
| `ansys_smoke_test.py` | Prove a licensed solver is reachable and solves. |
| `ansys_license_probe.py` | Query the license server and report what is available. |
| `ansys_size_probe.py` | Find the largest model the license tier will solve. |
| `build_plane_adapters.py` | Build the user-programmable-feature libraries for fabric materials. |
| `validate_plane_adapters.py` | Check the built libraries load and return the expected stiffness. |
| `validate_mesh_import.py` | Check an imported object mesh before running it. |
| `freeze_mesh_inputs.py` | Pin mesh inputs so a rerun reproduces a result. |
| `probe_plane_contact.py` | Build a plane model and report contact state without a full run. |

## Benchmarks

`benchmark_projection.py`, `benchmark_solvers.py`, `benchmark_contact.py` and
`benchmark_plane_speed.py` measure the CPU and GPU paths.
[Performance](../docs/performance.md) records what they have reported.
