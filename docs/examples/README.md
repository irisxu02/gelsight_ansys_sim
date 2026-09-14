# Examples

[Documentation](../README.md) · [Usage](../usage.md) · [Materials and contact](../materials-and-contact.md)

## Presets

These JSON configurations define the current example inputs. Materials and
lighting are provisional and uncalibrated.

| Preset | Motion | Object |
|---|---|---|
| [Sphere press](../../configs/sphere_press.json) | Press to 1 mm, then release | Rigid 3 mm radius sphere |
| [Sphere slide](../../configs/sphere_slide.json) | Press to 1 mm, slide 0.8 mm, then release | Rigid sphere, friction 0.5 |
| [Sphere twist](../../configs/sphere_twist.json) | Press to 1 mm, rotate through 5°, 10°, and 15°, then release | Rigid sphere, friction 0.5 |
| [Soft sphere press](../../configs/soft_sphere_press.json) | Press and release | Deformable 50 kPa sphere, friction 0.5 |
| [Rough sphere slide](../../configs/rough_sphere_slide.json) | Press, slide, and release | Deformable 2 MPa sphere, friction 0.9 |

The presets use a nominal Mini sensor with a uniform 36 × 30 × 8 gel mesh, a 320 × 240 rectified pinhole image, and 63 material-textured dots.
Add `--refine-contact` to a `run` command to enable the optional local contact
mesh shown in the [loading preview](../rendering/README.md).
See [sensor alignment](../sensor-alignment.md) for FOV and marker conventions.

## Trajectory conventions

Depth is commanded indenter or grip travel from nominal first touch. A deformable
sphere absorbs part of that travel, so its gel indentation can differ from a
rigid sphere's at the same command. Time indexes quasi-static load steps.

For sphere rotation, the geometric outline remains unchanged; friction produces
tangential deformation and torque. The sphere-twist check measures positive
z torque and counterclockwise material-marker circulation. All sphere presets
use the same 100 kPa, ν=0.49 Neo-Hookean gel.
The silicone coating is mechanically homogenized into that gel.
RGB is raw-style by default; use `--subtract-background` for a signed difference view.

The rough-slide preset changes both object stiffness and friction. It does not
resolve asperities and does not use ANSYS's no-slip rough-contact option.
[Object and contact assumptions](../materials-and-contact.md)

## Imported object examples

These presets use the [included block meshes](../../assets/meshes/README.md)
with the standard uniform gel, camera, and marker layout. Geometry, object
material, friction, and trajectory are independent config fields.

| Preset | Motion | Object | Validation key |
|---|---|---|---|
| [Imported rigid press](../../configs/imported_rigid_press.json) | Press 0.3 mm and release; 6 poses | Rigid STL surface, 12 triangles | `imported_rigid` |
| [Imported soft press](../../configs/imported_soft_press.json) | Press 0.3 mm and release; 6 poses | 50 kPa Neo-Hookean JSON volume, 48 hex elements | `imported_soft` |

Both use Coulomb friction μ = 0.5. The soft volume has named bottom contact
faces and top grip nodes. Its top grip follows the prescribed translation.
Validation checks recovery after release. The block measures 3 × 3 × 2 mm.

```bash
python scripts/run_simulation.py run --config configs/imported_rigid_press.json --render-scale 4 --output outputs/block_press
```

Use the table's validation keys with `validate_simulation.py` and
`export_examples.py`. The detached queue includes both presets and exports
checked PNGs and GIFs under `docs/examples/<config_name>/`.
These source meshes demonstrate import and fixture handling; their resolution
does not establish contact convergence. A sharp feature such as the supplied
pyramid's tip needs a gel refinement comparison before it can be slid: on the
uniform mesh the tip is carried by one or two elements and the solve is lost
within the first millimetre.

See [use your own object mesh](../usage.md#use-your-own-object-mesh) for the workflow,
and [mesh formats and placement](../configuration.md#custom-object-meshes) for the
input contract and supported material/motion combinations.

## Plane press-and-slide specifications

[`configs/material_plane_slide/suite.json`](../../configs/material_plane_slide/suite.json)
and its seven case files specify a material comparison with an oversized plane.
The shared launcher resolves these geometry/material configs through
`python scripts/run_simulation.py run --config PATH --render-scale 4`. Fabric and sticky
cases also need the native adapter libraries. See
[implementation and validation](../plane-material-adapters.md).

| Case config | Main variation |
|---|---|
| [Rigid reference](../../configs/material_plane_slide/rigid_reference.json) | Smooth rigid plane with baseline friction |
| [Soft rubber](../../configs/material_plane_slide/soft_rubber.json) | Nearly incompressible Neo-Hookean solid with relaxation |
| [Compressible foam](../../configs/material_plane_slide/compressible_foam.json) | Ogden hyperfoam with shear and bulk relaxation |
| [Fluffy fabric](../../configs/material_plane_slide/fluffy_fabric.json) | Effective directional compaction, shear, and friction |
| [Slippery surface](../../configs/material_plane_slide/slippery_surface.json) | Lower friction with reference geometry and bulk |
| [Rough surface](../../configs/material_plane_slide/rough_surface.json) | Moving sinusoidal topography with reference friction |
| [Sticky surface](../../configs/material_plane_slide/sticky_surface.json) | Reversible attraction and adhesive shear resistance |

Parameter values, physical meanings, expected signatures, and limitations are
in [Plane material specifications](../materials-and-contact.md#plane-material-specifications).

### Rigid plane, 1 mm slide at 5 N

`plane_rigid_short_slide_5n/` is the first plane slide to run to completion,
and the run whose measurements set the shipped protocol. It is a diagnostic,
not a preset: the rigid reference pressed to a commanded 5 N (0.030 mm
travel-driven preload, then load control), held, slid 1 mm — accelerating into
5 mm/s over 0.2 s, the slide integrated with mass at 0.1 ms from 50 ms in (its
first 50 ms are pure stick and were solved quasi-statically at 2 ms) — and held
to 4 s. Its resolved `config.json` and `summary.json` remain in the local full
export; Git tracks only this diagnostic's preview, force plot, and animation.
The shipped presets slide 2 mm, with mass integrated from 10 ms before the slide
starts. They do not reproduce this diagnostic's exact schedule.

| | |
|---|---|
| Frames | 44 over 0–4 s; every 0.05 s through the slide |
| Travel at 5 N | 0.093 mm |
| Tangential force at 1 mm | 2.15 N, ratio 0.43 against μ_k = 0.45 |
| Sliding points at the end of the slide | 1488 of 4080; all re-stick within 0.1 s of the platen stopping |
| Residual shear locked in during the hold | 2.18 N |

The slide was carried across three checkpoint resumes (t = 3.16, 3.2, 3.3), and
frame 34 (t = 3.2) was rendered from the result file afterwards; both are
recorded in `summary.json`. That frame's difference field was regenerated once
more when an audit found the first offline render had measured it against
itself rather than the unloaded reference; every frame now satisfies
`rgb_difference_int16 = image − unloaded_reference`, which the exporter checks. [Convergence](../convergence.md) explains what the
run established and why the slide has to be integrated with mass.
All values are illustrative and uncalibrated. The suite fixes the same uniform
100 kPa, ν=0.49 gel and camera/marker settings across cases.

### Plane geometry and loading sequence

The specimen is a **60 × 35 mm slab**, 3 mm thick (10 mm for the foam), with a
nominally planar bottom face. A rigid platen drives its top face; lateral faces
are free. This finite thickness allows rubber, foam, and fabric to deform. The
plane covers the whole nominal 25.25 × 20.75 mm sensor throughout the 10 mm x
travel, including the region outside the camera FOV.

The normal direction is load-controlled. Initialization presses the platen
0.030 mm past first touch over 2 s - the one travel that is still a command,
since a load cannot close an open gap - and hands the platen over to load
control. Material and contact history carry into the recorded sequence;
initialization states are excluded from its image frames.

| Recorded time | Phase | Normal load | x travel |
|---|---|---|---|
| 0–2 s | Press | Ramp from 1 N to 5 N | 0 |
| 2–2.3 s | Hold | 5 N | 0 |
| 2.3–2.8 s | Slide | 5 N | Accelerate to 5 mm/s over 0.2 s, then continue to 2 mm total travel |
| 2.8–3.1 s | Hold | 5 N | 2 mm |

There is no release or lift-off phase. Platen travel is an outcome, read back
from the solve each substep and recorded as `depth_m`: the rigid reference
reaches 5 N at 0.093 mm, foam much deeper. The slide, from 10 ms before it
starts, is integrated with the gel's mass at a 0.1 ms time step (see
[Convergence](../convergence.md)); the press and holds are quasi-static.
The sampling interval is 0.01 s throughout, giving 311 recorded frames and
362 mechanical checkpoints. Time represents physical loading duration in this
suite; the sphere/flat examples use rate-independent quasi-static load steps.

The slide is 2 mm and the holds are 0.3 s because of what they cost and what
they show. Transient sliding converges about 16,800 substeps per second, so
distance is the price of the protocol; the completed rigid reference reached
steady sliding, its first stick-slip release and its re-stick well inside 1 mm.
The holds bracket the slide rather than settle the material: re-stick happens
within 0.1 s of the platen stopping, while the viscoelastic specimens have a 2 s
Prony branch that no hold in this protocol waits out. Both holds are the same
for every specimen, so the comparison is between materials under one schedule,
not between materials at equilibrium.

### Continuous contact requirement

Full contact means continuous contact over the nominal sensor footprint at
a macroscopic scale. The config requires the plane to cover the deformed
sensor surface with at least 2 mm edge margin, and total repulsive normal
force to remain at least 0.01 N. Every 1 × 1 mm bin in sensor material
coordinates inside the camera's field of view must carry at least 1 µN of
repulsive contact force; a slide unloads the gel's free corners, which lie
outside every frame, so bin activity over the whole surface is recorded in
each frame but not gated. Bins are clipped at the sensor boundary.

These checks apply to all recorded frames and converged substeps. Microscopic
asperity gaps are allowed; tangential slip and local separation remain valid
contact behavior. A sequence that loses the required coverage must be rejected.
The contact contract preserves separation and sliding and rejects inadequate
coverage. It does not permit permanent bonding or automatic preload changes
as substitutes. Full recorded coverage is checked before export.

The suite defaults to a common uniform 36 × 30 × 8 gel mesh and a maximum
physical time increment of 0.01 s outside the 0.0001 s inertia window.
GPU work is verified when solver GPU use is requested. Its execution contract rejects
unsupported material/formulation/GPU combinations. Specified outputs include
RGB, material-marker motion, forces/torques, pressure, slip, coverage, and
specimen deformation.

## Exported snapshots

The detached queue discovers all registered presets with **1280 × 960 CUDA
rendering** (`--render-scale 4`). All presets default to the standard uniform
gel mesh; plane objects use independent simplified meshes. The queue
exports a case only after its numerical and data-integrity checks pass.
The seven plane cases use their own validation path and the 311-frame protocol.

Each local export is placed in `docs/examples/<config_name>/`, replacing its
previous generated dataset only after validation and checksum verification.
Local export folders contain numeric data, raw images, four-panel and raw/subtracted GIFs,
configuration, metrics, and a SHA-256 manifest. Temporary solver files and logs
stay under ignored `outputs/`. A preset file does not imply a completed solve.

The [0.8 mm rendering preview](../rendering/raw_vs_subtracted_0p8mm.png)
shows a sphere press with the locally refined mesh and raw/subtracted optical views.
[Comparison GIF](../rendering/raw_vs_subtracted_0p8mm.gif) and
[four-panel GIF](../rendering/loading_preview.gif) show loading only; release has
not been simulated for this preview. See [preview scope](../rendering/README.md).

## Exporting new snapshots

After [licensed validation](../verification.md#run-the-checks), export complete
passed runs with:

```bash
python scripts/export_examples.py --validation outputs/validation/validation.json
```

A complete export includes surface states, whole-body displacement and meshes,
camera/force fields, images, configuration, plot scales, and metrics.
`manifest.json` records SHA-256 checksums for exported files. Raw solver/restart files and local license
settings stay outside public exports.

Full exports remain available locally. Git includes only Markdown and the
curated `preview.png`, `process.gif`, `force_curve.png`, `raw_vs_subtracted.png`,
`raw_vs_subtracted.gif`, and optional `tactile.gif` assets. The default ignore rule
excludes all other per-case artifacts, including raw arrays, frame directories,
CSV metrics, generated JSON, and HTML viewers that depend on the local frames.
Queue status and other generated JSON under `docs/` are also local-only. Source
presets remain version-controlled under `configs/`.

A fresh clone contains the visual gallery. Run the commands above to generate
the full dataset for analysis, replay, or the interactive report. Git LFS is not
required. The exporter does not publish to a remote repository.
