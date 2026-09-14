# Run, configure, and replay

[Documentation](README.md) · [Setup](getting-started.md) · [Examples](examples/README.md)

Run commands from the repository root with the project environment active.
On Windows, an inactive environment can be addressed explicitly with
`.\.venv\Scripts\python.exe`. On Linux, add `--exec-file` with the path to
Linux MAPDL whenever starting a new mechanics solve.

## Run a preset

```bash
python scripts/run_simulation.py run --config configs/sphere_press.json
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --render-scale 4
```

All shipped presets use the same command and schema-3 input format. Select
`object.geometry`, an `object.material` case and its parameter overrides, and
surface/contact settings in the config. See the [configuration guide](configuration.md).
Add `--dry-run` to resolve and check a config without starting ANSYS.

For native Linux MAPDL:

```bash
python scripts/run_simulation.py run \
  --config configs/sphere_press.json \
  --exec-file /usr/ansys_inc/v252/ansys/bin/ansys252 \
  --output outputs
```

Use another configuration from the [example catalog](examples/README.md) to change
the trajectory or object. With `--solver-gpu`, add `--allow-unlisted-gpu` when the ANSYS device
allowlist requires the documented override. `--cpu` runs both mechanics and
optics on the CPU. The equivalent installed entry point is `gelsight-ansys`.

A new run gets a unique timestamped directory. Sphere/flat runs start with an
unloaded reference. Plane recordings start after preload and save their unloaded
reference separately. Recorded frames follow the configured trajectory. Automatic
solver substeps are distinct from saved animation frames. Completion requires
convergence, preserved loading history, force consistency, and the configured
GPU checks. [Solver lifecycle](architecture.md#solver-lifecycle-and-contact-results)

## Use your own object mesh

Start from [`imported_rigid_press.json`](../configs/imported_rigid_press.json)
for an STL/OBJ/JSON rigid surface, or
[`imported_soft_press.json`](../configs/imported_soft_press.json) for a deformable
JSON hex volume. Both use the same `run --config` command as generated shapes.

1. Copy the chosen preset to `configs/my_object.json` and give it a unique `name`.
2. Set `object.geometry.file`, source `units`, `transform`, `clearance_m`, and
   `reference_point_m` for your mesh. File paths resolve relative to the config.
   For a deformable volume, also select its contact face set and grip node set.
3. Choose `object.material`, `contact.friction`, and the direct `trajectory`.
   The top-level `material` describes the gel; it is independent of the object.
4. Check the input, then run at the desired optical resolution:

```bash
python scripts/run_simulation.py run --config configs/my_object.json --dry-run
python scripts/run_simulation.py run --config configs/my_object.json --render-scale 4 --output outputs/custom_object
```

The gel top is z=0; placement must leave the object's lowest point at its
positive clearance. The first pose is unloaded, with `depth_m = -clearance_m`
and zero x/y/twist. Subsequent positive depth presses into the gel; x/y specify
lateral travel in meters. See the [placement and format contract](configuration.md#custom-object-meshes)
for the quaternion convention, boundary sets, and supported material/motion combinations.
`--dry-run` checks input validity; solver convergence is checked during the run.

Runs save the same tactile images, marker motion, forces, and `process.gif` as
other contact examples. Resolved configs embed the imported geometry so replay
uses the saved mesh. The [detached queue](#detached-high-resolution-example-queue)
includes the two supplied imported-object press presets. A custom config can run
directly without registration; adding it to the curated queue requires its own
validation/export entry.

## Mechanical steps and saved frames

For material comparisons, saved images and mechanical checkpoints can be sampled
independently. To keep the existing solve checkpoints but save a frame every
0.05 s outside the slide:

```bash
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --sample-interval-s 0.05 --render-scale 4
```

A plane preset that was interrupted - by a failure, a stopped worker, or a fix
to the code that reads its results - can be continued instead of solved again:
`validate_plane.py --resume-from <directory of earlier runs>` picks the furthest
attempt the [restart rules](convergence.md) accept, and the detached queue
passes its own and the previous queue's run directories automatically when
started with `-ResumeFrom <previous queue directory>`. Anything the rules refuse
falls back to a fresh run with the reason printed, so a changed mesh or a
changed protocol is never silently continued.

`--sample-interval-s` controls saved states, images, and GIF frames.
`--solve-interval-s` controls mechanical checkpoints; ANSYS can take smaller
adaptive substeps inside each checkpoint interval. `--maximum-time-increment-s`
limits those internal steps. A transient window (the slide, in the shipped
presets) keeps its own sampling and solve intervals, so the command above
records 104 frames over 362 checkpoints rather than 63: the press and holds are
sampled at 0.05 s, the slide still at 0.01 s. Every converged substep still
undergoes the contact coverage checks, including states between saved frames.
Contact/backing force balance is required outside inertia windows; inside them
the residual is recorded. Saved-frame pilot-load and raster checks remain active.
See [acceptance rules](dataset.md#metrics-and-acceptance).
Saved images use solved states, with no interpolation of
deformation or forces. The saved-frame interval must be an integer multiple of
the solve interval, and both must divide the span they apply to.

To benchmark reduced mechanical work and a smaller deformable object mesh:

```bash
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --solve-interval-s 0.02 --sample-interval-s 0.02 --maximum-time-increment-s 0.02 --object-element-size-m 0.00075 --render-scale 4 --stop-after-s 0.1
```

The latter is an engineering probe, not an exported full example. It retains the
physical loading duration, displacement history, material laws, gel mesh, and
image resolution. The candidate settings require a force, deformation, contact,
and relaxation comparison before replacing the baseline. Protocol keyframes
remain mechanical checkpoints so changes in loading rate are preserved.

## Resume a saved solve

Resume from a separate copy of the saved ANSYS files:

```bash
python scripts/run_simulation.py resume --run outputs/PREVIOUS_RUN --output outputs/continued
```

For plane runs, `--config PATH` can supply future motion or sampling changes.
The saved frame times, completed mechanical checkpoints, preload history, mesh,
materials, contact, solver, and optics must remain compatible. The loader rejects
changes to solved motion before launching ANSYS. `--exec-file`, `--libraries`,
and a plane `--stop-after-s` are available on the resume command.

Plane restart requires the original unloaded reference, saved surface and body
states, mesh, and ANSYS `.rdb`, `.ldhi`, `.rst`, and `.rnnn` files. Use the same
ANSYS version that generated them. The adapter compares restored displacements,
forces, and contact slip against the saved state, then continues the nonlinear
restart so material and friction history are retained. An image or displacement
field alone cannot supply that history. [ANSYS restart requirements](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_bas/Hlp_G_BAS3_12.html).

A completed preload-only run can continue into its remaining recording. Its
saved frame zero and unloaded optical reference are retained. The default
material-comparison protocol ends in a hold; use an explicit
[release phase](configuration.md#plane-release-phase) when unloading is required.

## Compute allocation

The default uses four CPU cores for the sparse ANSYS solve and CUDA for surface
projection, camera sampling and RGB rendering. This is the measured faster
allocation on the tested workstation. All presets default to the standard uniform 36 × 30 × 8 gel mesh.
Plane setups can opt into whole-surface refinement with `discretization.gel_mesh`.

| Option | ANSYS mechanics | Projection and rendering |
|---|---|---|
| Default, or `--solver-cpu` | Four CPU cores | Configured backend; CUDA in all presets |
| `--solver-gpu` | Three CPU cores plus one GPU | Configured backend; CUDA in all presets |
| `--cpu` | Four CPU cores | CPU |

For the tested consumer GPU, ANSYS offloading also needs `--allow-unlisted-gpu`.
This override is unnecessary for CUDA projection/rendering. Explicit hardware
options select the tested allocations; custom JSON core counts remain available
when those options are omitted and the license permits them.

`--equation-solver mixed` selects the alternative equation algorithm. It passed
the small sphere benchmark but was slower; `sparse` remains the default. This
option changes neither the gel constitutive law nor the element formulation.
Per-frame `summary.json` entries include `timings` for solving, extraction,
projection, rendering and saves. `report_elapsed_s` records plot/GIF generation.
See [performance and limits](performance.md).

## Optional local contact refinement

The default configuration and all supplied presets use a uniform 36 × 30 × 8 mesh,
with approximately 0.70 × 0.69 × 0.50 mm cells. Add `--refine-contact` to a new
general-contact solve to use the locally refined mesh shown in the saved preview:

```bash
python scripts/run_simulation.py run --config configs/sphere_press.json --refine-contact
```

This selects 0.3 mm surface cells over x = ±3.6 mm and y = ±3.0 mm, graded outer
transitions, and through-thickness bias 2 (about 0.178 mm at the top). It preserves
the configured gel dimensions and element counts. The nominal presets retain
8,640 elements. Custom dimensions/counts must accommodate that refinement region;
incompatible configurations are rejected before ANSYS starts.

From Python, use `Config.load(path).with_contact_refinement()` for general contact.
Plane runs select gel refinement through their setup's `discretization.gel_mesh`;
see [plane mesh controls](plane-material-adapters.md#numerical-controls-and-resources).
The effective mesh settings are saved in the run's `config.json`. Explicit mesh settings in a
custom JSON are also respected without the flag. Refinement changes mechanics
and therefore applies to `run`; `render` retains the mesh of the saved solve.
The optical smoothing improvement is enabled for either mesh choice.

## Inspect results

Open the run's `report.html` locally to view the four-panel animation, inspect
individual RGB frames with the slider, and read force curves. Keep it beside
its `images/` folder and other outputs.
GitHub displays HTML source; PNG and GIF files can be viewed directly there.

| Output | Use |
|---|---|
| `preview.png` | Representative loading frame |
| `process.gif` | Complete RGB, deformation, pressure, and marker-panel cycle |
| `images/frame_XXXX.png` | Lossless tactile RGB frames used by the slider |
| `metrics.csv`, `summary.json` | Frame measurements, run status, and GPU evidence |
| `states/`, `fields/`, `bodies/`, `solid_mesh.npz` | Surface, image-grid, and whole-body numerical data |
| `config.json`, `camera.json`, `visualization.json` | Model, projection, and plot conventions used by the run |

The [dataset reference](dataset.md) defines shapes, signs, and units. Raw solver
files and private error logs are separate from portable example exports.

## Visualization storage

Default runs save one four-panel animation (`process.gif`), one representative
still (`preview.png`), and the force plot. They omit the duplicate
`preview.gif`, per-frame diagnostic panel PNGs, and the separate RGB-only GIF.
The report's slider reads existing lossless RGB frames; the four-panel GIF
shows the complete saved sequence.

The optional outputs can be enabled in the configuration:

```json
"visualization": {
  "marker_scale": 10.0,
  "marker_key_um": 100.0,
  "save_panel_frames": false,
  "save_tactile_gif": false
}
```

Set `save_panel_frames` to `true` to retain `panels/frame_XXXX.png` and use a
four-panel frame slider. Set `save_tactile_gif` to `true` to also save
`tactile.gif`. Both default to `false`, including when absent from older configs.
The numerical NPZ files, lossless RGB images, metrics, and raw solver files
are retained regardless of these settings. No trajectory frames are dropped.
Existing run directories are not automatically compacted.

Diagnostic plots can be regenerated from saved fields and images without
solving or rendering the sensor again. This updates only the report artifacts
and selected-metric CSV in the specified run:

```python
import json
from dataclasses import replace
from pathlib import Path
from gelsight_ansys.artifacts import build_report
from gelsight_ansys.config import Config

run = Path("outputs/YOUR_COMPLETED_RUN")
config = Config.load(run / "config.json")
config = replace(
    config,
    visualization=replace(config.visualization, save_panel_frames=True),
)
metrics = json.loads((run / "summary.json").read_text())["frames"]
build_report(run, config, metrics)
```

`visualization.json` records the report assets actually generated. Regenerating a
report does not remove assets from earlier generations. Example exports include
only the required assets and those selected by that metadata.

## Raw RGB and background subtraction

The default `optics.model="taxim"` and `optics.render_mode="raw"` use a measured
example-sensor background plus the normal-dependent change in optical response.
`optics.response_gain` scales that change (default 2).
`optics.response_smoothing_bins` regularizes the angular calibration table
(default sigma 2 bins); cubic interpolation keeps its gradients continuous.
This acts on the optical model, not on saved mechanical fields or marker motion. This is a nominal appearance
model; see [asset provenance](../src/gelsight_ansys/data/mini/PROVENANCE.md).

Add one argument to either command:

```bash
python scripts/run_simulation.py run --config configs/sphere_press.json --subtract-background
python scripts/run_simulation.py render --run docs/examples/sphere_press --subtract-background --backend cuda
```

The reference is the first unloaded raw frame, including its undeformed markers.
The subtraction is signed per channel: uint8 display is
`clip(round(128 + (I - I0)/2), 0, 255)`. Gray 128 means no change. Bright/dark marker
pairs show where a marker moved. Exact `I-I0` is saved as `rgb_difference_int16`
in every field file, in both modes. PNGs and GIFs use the selected display mode;
mechanical arrays and forces do not change. Raw means simulated camera RGB,
not a camera's Bayer RAW file format.

Set `optics.model="analytic"` for the configurable three-light renderer.
A supplied `background_image` must be marker-free and match image dimensions.
Do not embed stationary photographed markers beneath the moving material texture.

## Change the configuration

Copy a preset and edit its JSON fields before solving:

| Configuration section | Controls | Reference |
|---|---|---|
| `gel`, `material` | Dimensions, mesh, constitutive law | [Modeling](modeling.md) |
| `indenter` | Object compliance and friction | [Materials and contact](materials-and-contact.md) |
| `trajectory` | Grip travel, translation, and rigid-object rotation | [Examples](examples/README.md) |
| `camera`, `optics` | FOV, projection, lighting, and material markers | [Sensor alignment](sensor-alignment.md) |
| `solver` | Nonlinear controls and CPU/GPU choices | [Verification](verification.md) |
| `visualization` | Marker arrows, reference key, and optional report assets | [Visualization scales](dataset.md#marker-visualization-and-complete-cycle-gifs) |

All physical input uses SI units. Positive depth is commanded grip travel into
the sensor, not necessarily the gel's deformation when the object is soft.
The example material and optical parameters remain provisional.

## Replay saved mechanics

For a complete passed run, re-render its saved states without starting ANSYS:

```bash
python scripts/run_simulation.py render --run outputs/YOUR_RUN --backend cpu
python scripts/run_simulation.py render --run outputs/YOUR_RUN --backend cuda
```

Replace `outputs/YOUR_RUN` with the actual run directory. The CUDA command needs
Warp and a working NVIDIA driver. Replay creates a new output directory and
retains the original mechanics evidence.

Edit the copied run's `config.json` optics or visualization settings before
replay. Changes to the gel, object, contact, or trajectory require a new
mechanics solve. `optics.background_image` must match the image dimensions; use
a marker-free or inpainted background because existing photographed marks are
not independently tracked.

`visualization.marker_scale` changes diagnostic arrow length and
`visualization.marker_key_um` controls its reference key. Neither changes the
saved physical displacement nor the tactile RGB motion. All frames in a process
GIF share the same plot limits.

## Higher-quality previews and performance measurements

For preview generation, use the optional locally refined mechanics and render
new optical samples at four times the nominal camera resolution:

```bash
python scripts/run_simulation.py run --config configs/sphere_press.json --refine-contact
python scripts/compare_rendering.py --run outputs/YOUR_RUN --render-scale 4 --backend cuda
```

`--render-scale 4` generates 1280 × 960 tactile RGB at the same physical FOV and
material-marker positions. It reuses the saved mechanical state and does not
refine the finite-element mesh. Keep nominal-resolution performance measurements
separate from these higher-resolution presentation outputs.

Reproduce projection and matched solver measurements with:

```bash
python scripts/benchmark_projection.py --run outputs/YOUR_RUN --repeats 3
python scripts/benchmark_solvers.py --allow-unlisted-gpu --include-cpu-reference
```

The first command uses saved states only. The second runs sequential 0.4 mm
sphere loading/release cases with identical mechanics settings, comparing GPU
sparse/mixed solvers and optionally four CPU solver cores. It verifies force and
surface-displacement agreement as well as the normal run guards.

## Validate and export examples

Run [licensed validation](verification.md#run-the-checks) before exporting a new
example dataset. Then copy only passed runs into the public gallery:

```bash
python scripts/export_examples.py --validation outputs/validation/validation.json
```

The exporter checks completeness and file hashes, and excludes raw solver logs.
`python scripts/audit_examples.py` also checks every exported preset, complete
process GIF, raw-image/difference identity, marker flow, and GPU evidence;
`--run docs/examples/<folder>` audits a curated export that is not a preset.
The exporter itself refuses a run whose difference fields are not each frame's
image minus the unloaded reference.
`python scripts/compare_rendering.py` creates a raw-versus-subtracted illustration
from a completed raw run.
See [example exports](examples/README.md#exporting-new-snapshots) for local datasets and
Git inclusion rules.

## Detached high-resolution example queue

On Windows, start an independent queue with the Python environment that has
PyMAPDL and CUDA rendering installed:

```powershell
.\scripts\start_render_queue.ps1 -Python .\.venv\Scripts\python.exe -RenderScale 4
```

The launcher snapshots `src/`, `scripts/`, `configs/`, and `assets/` into a new ignored
`outputs/queue_<timestamp>/source/` directory and records source hashes. It uses
the Windows process service to create a worker that runs independently of the
launching shell. Windows must remain awake with access to the license server.
The worker does not restart automatically after a reboot or logoff.

Imported mesh files are resolved and embedded in the snapshot configs before
launch, including meshes outside the repository. Later source-file edits do not
change that batch.

The worker runs one ANSYS validation at a time, applies the same optical scaling
as `compare_rendering.py --render-scale 4`, and saves 1280 × 960 images and camera
fields. It preserves physical FOV, dot attachments, contact history, and preset
meshes. Four-panel previews/GIFs use 2200 × 1700 canvases; raw/subtracted comparisons
use 2704 × 1272 canvases. GIFs use a shared palette and dithering; PNGs retain
full RGB color. Higher optical resolution does not refine finite elements.

Each successful case replaces `docs/examples/<config_name>/` after the full-cycle
physics checks, export checksums, and data audit pass. Raw solver files and error
logs stay in the queue work directory. Failed cases are recorded and the next
case proceeds. The seven plane-material configurations are
selected by object geometry; see [material comparison and native adapters](plane-material-adapters.md).

Read `docs/examples/queue-status.json` for progress; it is refreshed during solves
and between stages. The private work directory contains `worker.log`, per-case
logs, `snapshot-manifest.json`, and a `resume-command.txt`. After an interruption,
run the saved command to reuse passed validation results and retry failed cases.
Interrupted solves without a completed validation record start a new solve;
passed cases are skipped. A process lock rejects a second worker on the same queue.

Work and example directories must share a drive so checked folders can be
promoted without copying a partial dataset into the public folder. The queue
exports locally; it does not push to GitHub.

## Independent plane-object mesh

The plane solver simplifies objects by default while retaining the gel mesh.
Smooth rigid planes use one exact facet; deformable slabs use 0.5 mm cells and
graded thickness. Textured targets keep their resolved roughness.

```bash
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --render-scale 4
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --object-element-size-m 0.00025 --render-scale 4
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --object-mesh matched --render-scale 4
```

See [mesh counts and validation scope](plane-material-adapters.md). A coarser
object changes the numerical approximation, so compare forces and deformation
when choosing its resolution.
