# Dataset reference (schema version 1)

[Documentation](README.md) · [Usage](usage.md) · [Architecture](architecture.md)

## Run files and coordinates

The files below describe local run/export data. The Git repository includes
curated PNG/GIF previews and source presets; numerical arrays, per-frame images,
generated metadata, and interactive reports are generated locally and ignored.
See [example sharing rules](examples/README.md#exporting-new-snapshots).

Each run contains:

```text
config.json              resolved, reusable input configuration
camera.json              nominal intrinsics, FOV, pixel pitch and conventions
summary.json             status, units, device evidence, metrics for each frame
metrics.csv              selected frame metrics for analysis
report.html              local interactive viewer (no web service required)
preview.png              representative force/shear/torque frame
force_curve.png          loading curve and force components
images/frame_XXXX.png    uint8 RGB (H, W, 3)
process.gif              all four panels over the full cycle
visualization.json       fixed color limits, marker scale, GIF timing
solid_mesh.npz           gel and optional object reference mesh
bodies/frame_XXXX.npz    whole-body gel and optional object displacement
states/frame_XXXX.npz    native gel surface state and result identity
fields/frame_XXXX.npz    raster fields and tracked markers
fields/frame_XXXX.json   frame metrics and GPU evidence
solver/                  raw MAPDL inputs, outputs, results and restart files
private/error.log        traceback on failed runs
background.png           copied optical background, when configured
```

Optional report outputs are `panels/frame_XXXX.png` (four-panel stills) and
`tactile.gif` (RGB-only animation). Enable them with
`visualization.save_panel_frames` and `visualization.save_tactile_gif`; both
default to `false`. `preview.gif` is no longer generated because it duplicated
`process.gif`. These settings do not alter the numerical files, lossless RGB
frames, or solver outputs. See [visualization storage](usage.md#visualization-storage)
for regeneration from saved data.

Arrays load with `numpy.load(path, allow_pickle=False)`. SI units apply throughout:
metres, newtons, pascals, and N m. The sensor frame has x right, y up, and z outward
from the gel. The reference gel surface is z=0; the backing is at
z=-gel thickness. Image rows increase
in the -y direction. Positive prescribed depth indents below z=0.

## Native SurfaceState

Let N be the number of surface nodes, Q the number of contact quads, and T=2Q.

| Field | Shape / interpretation |
|---|---|
| `time_s`, `load_step`, `substep` | Scalar loading time and ANSYS result identity |
| `source` | `ansys` or `unloaded_reference` |
| `node_ids` | (N,), original 1-based MAPDL node IDs |
| `reference_m`, `displacement_m` | (N,3), reference position and solved displacement |
| `triangles`, `quads` | (T,3), (Q,4), zero-based local surface connectivity |
| `contact_force_n` | (N,3), integrated equivalent nodal contact load on gel |
| `contact_couple_nm` | (N,3), contact couples at the exported surface; zero for solid gel states |
| `contact_pressure_pa` | (Q,), ANSYS element-average compressive pressure |
| `contact_status` | (Q,), maximum over integration points: 0 far, 1 near, 2 sliding, 3 sticking |
| `contact_elastic_slip_m` | (Q,4), ELSI at four integration points; empty for historical missing output |
| `contact_integration_status` | (Q,4), status at the same four integration points |
| `contact_penetration_m` | (Q,), element contact penetration output |
| `backing_reaction_n` | (3,), summed fixed-backing reaction |
| `pilot_reaction_n`, `pilot_moment_nm` | (3,), rigid-pilot reactions or resultant reactions of the deformable sphere grip |
| `pilot_position_m` | (3,), current rigid pilot or virtual grip-center position |

Position is `reference_m + displacement_m`. Compression gives negative z contact
force on the gel. `normal_force_n = -sum(contact_force_n[:,2])` is positive during
normal indentation. Contact force opposes the backing reaction and matches the
prescribed-motion pilot reaction in the tested convention.

Raw volume stresses and strains at each requested load-step endpoint remain in
the MAPDL result file (`OUTRES,ALL,LAST`); the public NPZ
contract exports the sensor surface and whole-body nodal displacement, without duplicating volume stress/strain fields.

## Whole-body meshes and displacement

`solid_mesh.npz` contains `gel_reference_m`, `gel_hexes`, `gel_material_ids` (all 1),
and `surface_nodes`. With a deformable
sphere it also contains `indenter_reference_m`, `indenter_hexes`,
`indenter_surface_quads`, and `indenter_grip_nodes`. All connectivity and node
selections are zero-based local indices. `bodies/frame_XXXX.npz` contains
`gel_displacement_m` and, when applicable, `indenter_displacement_m`. These follow
the corresponding reference mesh ordering and include the analytical zero frame.
The body files and optical arrays always come from the same requested load-step endpoint.

For deformable objects, prescribed depth is grip travel, not measured gel
indentation. `max_indenter_deformation_m` measures displacement relative to the
commanded translation; `indenter_grip_displacement_error_m` checks the drive.
`max_sticking_elastic_slip_m` measures contact penalty slip among sticking points,
separate from physical marker deformation. A null value means no sticking point
is available. New summaries retain the mechanical configuration and replay rejects
changes to it, while allowing optics changes.

## Raster and marker arrays

| Field | Shape / interpretation |
|---|---|
| `position_m`, `displacement_m`, `normals` | (H,W,3), spatially sampled surface geometry |
| `normal_displacement_m` | (H,W), `-uz`, retains bulging outside contact |
| `shear_displacement_m` | (H,W,2), signed `ux,uy`; not accumulated contact slip |
| `contact_pressure_pa` | (H,W), sampled raw element-average pressure |
| `contact_status`, `slip_mask` | (H,W), sampled element status and status==2 |
| `valid_mask` | (H,W), pixel center lies on the projected gel surface |
| `pixel_force_n` | (H,W,3), exactly integrated reconstructed force in each pixel |
| `force_density_pa` | (H,W,3), pixel force / projected pixel area |
| `fov_force_n`, `raster_force_error_n` | (3,), independent clipped-FOV integral and raster residual |
| `marker_reference_m`, `marker_position_m` | (M,3), fixed reference attachments and deformed positions |
| `marker_pixel` | (M,2), projected (column,row) camera coordinates, possibly outside image |
| `marker_reference_pixel`, `marker_flow_pixel` | (M,2), reference pixels and apparent pixel displacement |
| `optical_position_m`, `optical_reference_m` | (H,W,3), current and material locations at camera rays |
| `rgb_difference_int16` | (H,W,3), signed raw RGB minus first unloaded raw RGB, intensity units |
| `optical_normals`, `optical_valid_mask` | (H,W,3), (H,W), ray-sampled shading normals and visibility |

For a partially covered pixel, `pixel_force_n` still includes its exact surface
intersection even if the center is outside `valid_mask`. Do not multiply this
array by the center-valid mask before integrating. Geometry outside the mask
contains placeholders. Pressure is sampled at pixel centers; it is not the
conservative force field and need not integrate to net normal force.

Marker displacement is `marker_position_m - marker_reference_m`. Subtracting the
reference image coordinates gives marker flow in pixels, including perspective dilation. Marker membership is
fixed by material attachment, not by whether contact pressure is positive.

For pinhole rendering, `optical_*` arrays and `marker_pixel` share the perspective
camera. Other raster arrays remain on the orthographic gel-plane grid. Their
pixel indices are not interchangeable at nonzero indentation. A camera pixel
maps back to gel coordinates through `optical_position_m`; the renderer samples
its dot texture at `optical_reference_m`. This preserves conservative force data
without interpreting camera perspective as a change in physical force density.

## Metrics and acceptance

`force_on_gel_n` uses native nodal loads. `moment_on_gel_nm` includes their
deformed lever arms and `contact_couple_nm`. Moment is about
the sensor origin. `center_of_pressure_m` uses signed z nodal loads and is `null`
when normal force is effectively zero. Off-FOV load is reported separately.

The launcher disables PyMAPDL no-abort mode. Each solve first requires `CNVG=1`, the requested result time, increasing
load-step identity, and no forced nonconverged continuation. It must then pass
contact/backing and contact/pilot force balance
within `max(balance_tolerance * norm(force), 1e-6 N)`. Default relative tolerance
is 2%. Raster force residual must be below `max(1e-8 * norm(force), 1e-10 N)`.
These tolerances verify internal consistency, not experimental accuracy.

`gpu_solver` stores solver activation and accelerated-work evidence. Frame zero
has no equation solve, so solver GPU activity is false there. CUDA optics still
renders the reference. Summary `gpu_mechanics_verified` requires positive GPU work
in the run. For optical replay it refers to the original solve, not new execution.

Raw solver files and private tracebacks can contain environment-specific details;
review them before sharing. Portable arrays, images and metric summaries do not
need those files for re-rendering.

## Marker visualization and complete-cycle GIFs

`visualization.marker_scale` defaults to 10. It multiplies arrow lengths in the
marker plot; it never modifies `marker_position_m`, `displacement_m`, force data,
or tactile RGB. The quiver reference arrow is labeled by the actual displacement
(`visualization.marker_key_um`, initially 100 µm). Arrow color uses the actual
in-plane magnitude in µm, rather than the total displacement dominated by depth.
`max_marker_in_plane_displacement_m` is also reported in frame metrics and CSV.
`max_marker_image_displacement_px` records apparent camera motion separately.

The deformation, pressure and marker color limits are computed across the entire
trajectory and held fixed in all panels. `process.gif` contains one annotated
panel for every saved frame, including initial clearance and final release.
When enabled, `tactile.gif` contains the corresponding RGB images; identical images may be
combined into a longer GIF frame. Endpoint pauses are at least 700 ms. Animation
timing is illustrative, and images are not interpolated into extra solver states.

`visualization.json` records scales, frame count, preview selection, and GIF
frame durations. The preview selects maximum absolute z torque for a twist,
maximum shear force for a slide, or maximum normal force for a pure press.
By default, the viewer shows the four-panel animation and an individual RGB
frame slider. With `save_panel_frames=true`, the slider updates all four panels
together. `visualization.json` records `frame_viewer_folder` (`images` or
`panels`) and `tactile_gif_saved` for exporters. Historical reports without these
keys use panel frames and a tactile GIF. Force plots include z torque in N mm;
native moment arrays and CSV remain in N m.


## RGB modes

Raw RGB is default. The subtraction display encodes signed differences as
`clip(round(128 + rgb_difference_int16/2), 0, 255)`; zero appears gray. The
reference includes the original marker texture, so marker motion contributes
to the difference. `config.json`, `summary.json`, and `visualization.json` record
the selected mode. Shading uses example-sensor optical assets supplied in the
package; this does not claim hardware calibration.

### Timing and high-resolution queues

New `summary.json` frame records include `timings` for solve commands, extraction,
projection, camera sampling, rendering, and saving. `report_elapsed_s` measures
final plot/GIF construction. `projection_device` and `render_device` identify the
Python GPU path independently of `gpu_mechanics_verified`. Default four-CPU ANSYS
runs correctly record solver GPU verification as false while projection and RGB
rendering use `cuda:0`.

Queued exports record the actual 1280 × 960 camera and scaled pixel marker
settings in `config.json` and `camera.json`; their physical FOV and dot layout
match the nominal 320 × 240 preset. Audit those exports with
`python scripts/audit_examples.py --render-scale 4` after all cases pass.
`raw_vs_subtracted.png` and `.gif`, when present, are included in the checksum
manifest. Per-frame PNGs and signed difference arrays retain full color/data;
GIFs are palette-limited visualizations.
