# Geometry, material, and contact configuration

[Project overview](../README.md) · [Usage](usage.md) · [Examples](examples/README.md)

All shipped simulation inputs use `schema_version: 3` and
`config_kind: "contact_simulation"`. Use one command for every preset:

```bash
python scripts/run_simulation.py run --config configs/sphere_press.json
python scripts/run_simulation.py run --config configs/material_plane_slide/soft_rubber.json --render-scale 4
```

`--dry-run` resolves references and checks configuration and supported combinations
without launching ANSYS. It does not establish native solver convergence or
license availability. `--render-scale`, `--subtract-background`, `--solver-cpu`,
`--solver-gpu`, and `--cpu` apply through the shared launcher.

## Independent selections

| Config field | Meaning |
|---|---|
| `object.geometry` | Shape, dimensions or imported mesh file; selects the mechanical adapter |
| `object.material.case` | Relative path to a reusable constitutive material case |
| `object.material.parameters` | Overrides for that material case |
| `surface` | Optional resolved surface geometry/texture; smooth by default for sphere/flat |
| `contact` | Friction and adhesion for the object–sensor pair |
| `setup` | Shared finite-slab sensor, fixture, protocol, mesh, and numerical settings |
| `gel`, `material`, `trajectory`, `solver`, `camera`, `optics` | Direct shared settings for the sphere/flat/mesh adapter; top-level `material` describes the gel |

For example, edit the object block in
[`soft_rubber.json`](../configs/material_plane_slide/soft_rubber.json):

```json
"object": {
  "geometry": {
    "shape": "plane",
    "width_m": 0.06,
    "length_m": 0.035,
    "thickness_m": 0.003
  },
  "material": {
    "case": "../materials/soft_rubber.json",
    "parameters": {
      "young_pa": 75000
    }
  }
}
```

This selects a 60 × 35 × 3 mm slab with the rubber case's Neo-Hookean model and
relaxation spectrum, overriding its instantaneous Young's modulus to 75 kPa.
Changing width affects geometry; changing the material case or parameters affects
constitutive behavior. Friction and adhesion are specified separately in `contact`.
The object geometry dimensions take precedence over shared setup specimen dimensions.

Material and setup paths are resolved relative to the simulation config file.
Moving a config requires updating its relative references. Overrides recursively
merge objects and replace arrays in full. Unknown material parameters are rejected;
source material-case files are never modified by overrides.

An inline material is also accepted instead of a case reference:

```json
"material": {
  "model": "neo_hookean",
  "parameters": {
    "young_pa": 50000,
    "poisson": 0.45,
    "formulation": "displacement"
  }
}
```

Place this block inside `object`. Select exactly one of `case` and `model`.
All values use SI units. Saved `config.json` records contain expanded material
values and require no external material-case files to read. All geometries use
one resolved `Config`; new result snapshots use schema 4 with
`config_kind: "resolved_contact_simulation"`. This result format is separate
from the schema-3 source presets. Earlier saved results can still be loaded.

Python callers use the same methods for every geometry:

```python
from gelsight_ansys.config import Config
from gelsight_ansys.pipeline import run

config = Config.load("configs/material_plane_slide/soft_rubber.json")
config = config.with_render_scale(4).with_solver(cores=4, gpu=False, require_gpu=False)
config = config.with_optics(render_mode="raw")  # CUDA remains the default backend.
config = config.with_plane_options(object_element_size_m=0.0005)
directory, summary = run(config, "outputs")
```

`with_plane_options` applies only to slab geometry. Common rendering and solver
methods operate directly on the resolved fields. The shape still determines the
supported fixture, material laws, and loading protocol.

## Reusable material cases

| Case file | Constitutive behavior |
|---|---|
| [rigid](../configs/materials/rigid.json) | Rigid object |
| [elastic_silicone](../configs/materials/elastic_silicone.json) | 50 kPa Neo-Hookean elasticity, ν = 0.45; no relaxation |
| [soft_rubber](../configs/materials/soft_rubber.json) | 50 kPa Neo-Hookean elasticity, ν = 0.49, plus shear Prony relaxation |
| [compressible_foam](../configs/materials/compressible_foam.json) | Ogden hyperfoam with shear and bulk Prony relaxation |
| [fluffy_fabric](../configs/materials/fluffy_fabric.json) | Effective orthotropic elasticity, nonlinear compaction, and relaxation |

These files use `config_kind: "object_material_case"` and are not simulation jobs.
They intentionally contain no geometry or friction law. The rough and slippery
rigid comparison presets share the rigid material case and change `surface` or
`contact`. The soft and stiffer sphere presets share the elastic-silicone case
and override Young's modulus where needed.

## Supported combinations

A shared config format separates choices; it does not imply every material law
is implemented for every mechanical adapter.

| Geometry | Bulk behavior | Contact and motion |
|---|---|---|
| `sphere` | Rigid, linear elastic, Neo-Hookean, or Mooney–Rivlin | Constant Coulomb friction; rigid rotation and translation; deformable translation |
| `flat` | Rigid | Constant Coulomb friction; rotation and translation |
| `mesh` | Rigid surface or linear/Neo-Hookean/Mooney–Rivlin hex volume | Constant Coulomb friction; rigid translation/z twist; deformable translation |
| `plane` | Rigid, Neo-Hookean with optional Prony relaxation, Ogden hyperfoam, or effective fabric | Constant or exponential velocity-dependent Coulomb friction, directional friction, roughness, and reversible adhesion; configured normal travel and x sliding |

`plane` means a finite slab with a top-face fixture. Its shared setup uses
physical-time preload and press/hold/slide/hold recording with coverage checks.
A sphere/flat trajectory uses the direct `trajectory` array and a different
fixture. Changing between these adapters also requires selecting a compatible
loading setup. A deformable sphere cannot currently use the foam/fabric models,
Prony history, or adhesive contact. Those combinations are rejected before a
solver starts.

Fabric bulk behavior and custom directional/adhesive contact require
`--libraries PATH_TO_NATIVE_LIBRARIES`. See [native adapters](plane-material-adapters.md).
The default is four CPU solver cores plus CUDA projection and rendering. Plane
objects use simplified meshes by default; `--object-mesh matched` and
`--object-element-size-m` control their discretization independently of the gel.

## Material-comparison sampling

The shared setup's `dataset.sample_interval_s` selects saved output times.
`dataset.solve_interval_s` optionally selects a finer mechanical checkpoint grid;
when absent it defaults to the saved interval. The saved interval must be an
integer multiple of the solve interval. Protocol keyframes are included even
when they fall between regular checkpoints. `solver.maximum_time_increment_s`
is the separate limit on adaptive ANSYS substeps.

For example, `sample_interval_s: 0.05` and `solve_interval_s: 0.01` retain 601
mechanical checkpoints but save 121 frames over the six-second interval.
Every converged internal state still receives the mechanical acceptance checks.
See [sampling CLI options](usage.md#mechanical-steps-and-saved-frames).

## Contact numerics

Every key under the setup's `contact_numerics` is read by the solver adapter,
and the setup is rejected if it declares one that is not; documentation goes in
keys ending in `_note`. What each becomes in ANSYS:

| Key | CONTA174 setting |
|---|---|
| `formulation` | `KEYOPT(2)`: `augmented_lagrange` (0) or `penalty` (1) |
| `sliding` | Must be `finite`; the elements are defined for finite sliding |
| `separation` | `KEYOPT(12)`: `allowed` (0) or `no_separation` (2) |
| `normal_stiffness_factor` | `FKN` |
| `penetration_tolerance_m` | `FTOLN`, absolute |
| `tangential_stiffness_factor` | `FKT` |
| `elastic_slip_tolerance_m` | `SLTO`, absolute |
| `pinball_radius_m` | `PINB`, absolute |
| `update_stiffness_each_iteration` | `KEYOPT(10)`: 2 when true, 0 when false |
| `stabilization_damping` | `FDMN`, `FDMT` and `KEYOPT(15)`; see [Convergence](convergence.md) |
| `symmetric_pair` | A second, reversed pair with `KEYOPT(8) = 2` |

The same rule applies to `solver`: its keys are the `Solver` fields plus
`maximum_time_increment_s`, which shapes the schedule, plus a free-text `note`.

## Control modes

The shipped presets share `suite.json`, which commands a normal *load* of 5 N,
accelerates into the slide, and integrates the slide with mass (see
`protocol.transient`). Load rather than travel is what makes the material
comparison a comparison: equal travel is not equal load, so a travel-driven
protocol compares materials at different forces and has to be refitted per
material. Under load control one protocol transfers to every specimen and the
travel each needs is recorded (`platen_travel_m`) instead of set. Contact is
still closed by a short travel-driven preload, which must stay below the target
on the stiffest specimen; `protocol.normal_control` switches the mode.

[Convergence](convergence.md) explains both control modes, why the slide is
integrated with mass, and how to size a transient window. A case that ships as
a worked example rather than a dataset preset can carry
`"status": "capability_example"`, which preset discovery and export leave alone.

## Custom object meshes

Set `object.geometry.shape` to `"mesh"` and use the same `run --config` command.
The implemented import paths are rigid STL/OBJ/JSON surfaces and deformable
JSON volumes containing eight-node hex elements. Imported meshes use constant
Coulomb friction and the direct `trajectory` array. Rigid objects support
translation and z-axis twist; deformable volumes support translation with a
prescribed grip. Linear, Neo-Hookean, and Mooney–Rivlin object materials are
supported. Plane protocols, Prony relaxation, foam/fabric laws, adhesion,
automatic volume meshing, and tetrahedral elements are not supported for imports.

See [use your own object mesh](usage.md#use-your-own-object-mesh) for the workflow.
Try the supplied source meshes:

```bash
python scripts/run_simulation.py run --config configs/imported_rigid_press.json --dry-run
python scripts/run_simulation.py run --config configs/imported_soft_press.json --render-scale 4
```

The two press examples indent a 3 × 3 × 2 mm block by 0.3 mm and release.
Both share the uniform gel and camera defaults; the soft block uses 50 kPa
Neo-Hookean elasticity. See [example motions](examples/README.md#imported-object-examples)
and [source assets](../assets/meshes/README.md). Imported geometry
needs numerical validation for each mesh and contact/loading combination;
[the licensed import check](verification.md#mesh-import-validation) compares
an imported reference target with the generated one.

A rigid object's config block is:

```json
{
  "object": {
    "geometry": {
      "shape": "mesh",
      "file": "../assets/meshes/block_surface.stl",
      "units": "mm",
      "clearance_m": 0.0001,
      "transform": {
        "translation_m": [0.0, 0.0, 0.0001],
        "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]
      },
      "reference_point_m": [0.0, 0.0, 0.0021]
    },
    "material": {
      "case": "materials/rigid.json"
    }
  }
}
```

Paths resolve relative to the config; absolute mesh paths are also accepted.
`units` is required and accepts `m`, `mm`, or `cm` for source vertices. All other
lengths remain in meters. The unit quaternion rotates about the source origin
after unit conversion, then `translation_m` places the object in gel coordinates.
The transformed object's minimum z must equal the positive `clearance_m`
(within 1 nm); the gel top is z=0. Placement is never silently recentered or
moved to contact.

`reference_point_m` is an explicit point in gel coordinates after placement.
It is the rigid motion pivot and the reference for exported reaction moments;
for a deformable object it defines the wrench reference. The first trajectory
pose must be unloaded: time 0, depth `-clearance_m`, zero x/y/twist. Subsequent
z travel is `-depth_m - clearance_m`, so depth 0 denotes first geometrical touch.
Rigid twist is about global z through the translated reference point. Initial
mesh orientation can use any unit quaternion.

### Surface files

ASCII and binary STL are accepted; coincident STL vertices are welded by exact
coordinate equality. OBJ accepts triangles and quads, including negative vertex
indices. Material/texture records do not select mechanical properties or load
external files. Larger polygons and non-surface OBJ records must be converted
before import. Vertex order determines the contact normals. Adjacent faces must
have consistent winding; closed components must face outward. Open patches are
allowed, with contact-facing normals supplied by the user.

A JSON surface uses `schema_version: 1`, `config_kind: "object_mesh"`, a
`vertices` array, and a `faces` array of three- or four-node faces. Indices are
zero-based integers. Alternatively, put faces in `face_sets` and select one with
`object.geometry.contact_face_set`.

### Deformable volume files

Use an `object_mesh` JSON file with `vertices`, `hexes`, `face_sets`, and
`node_sets`. Select both `contact_face_set` and `grip_node_set` in geometry,
and choose a deformable material. A surface file alone cannot define this body.
The following single-element mesh illustrates the file layout, in the units
selected by the simulation config:

```json
{
  "schema_version": 1,
  "config_kind": "object_mesh",
  "vertices": [
    [-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0],
    [-1, -1, 2], [1, -1, 2], [1, 1, 2], [-1, 1, 2]
  ],
  "hexes": [[0, 1, 2, 3, 4, 5, 6, 7]],
  "face_sets": {"contact": [[0, 3, 2, 1]]},
  "node_sets": {"grip": [4, 5, 6, 7]}
}
```

For this file, add `"contact_face_set": "contact"` and
`"grip_node_set": "grip"` to geometry. The hex order is the conventional bottom
loop followed by the corresponding top loop, as shown above. Contact faces must
be outward-oriented exterior quads of those hexes. The grip must contain at
least three non-collinear exterior nodes. Its three displacement components
follow the trajectory; all other non-contact boundaries are free. The volume
must be connected through shared faces.

Import checks reject nonfinite coordinates, invalid connectivity, duplicate or
degenerate faces/elements, inconsistent winding, non-manifold edges/faces,
invalid boundary selections, and hexes with nonpositive or near-singular
Jacobians at corners, Gauss points, or center. These checks do not establish
mesh convergence or detect every possible global self-intersection.

### Saved meshes and detached queues

Resolved `config.json` embeds the transformed SI mesh, connectivity, selected
boundaries, reference point, source basename and SHA-256, units, and transform.
`solid_mesh.npz` includes the corresponding object geometry. Re-rendering and
resume read this saved data without reopening the original mesh. Changes to
embedded mechanical geometry require a new solve.

The detached launcher resolves mesh inputs into its snapshot configs before
starting the queue, including files outside the repository. Later source edits
therefore do not alter that batch. The supplied imported presets are registered
for validation/export; adding a different preset to the curated queue still
requires a validation/export entry. Ordinary `run --config` accepts custom names.
Custom gel meshes require a separate backing and sensor-surface attachment
contract and are not implemented.

See [architecture extension points](architecture.md#custom-mesh-extension-points)
for the implementation boundaries.

## Extending supported combinations

Geometry adapters own the mesh, fixture, and state extraction. Material adapters
translate the resolved constitutive parameters into ANSYS definitions. Contact
adapters own the interface law and history. Add a capability only when all three
pieces support the requested combination, then validate force balance, material
history, contact, and mesh sensitivity. This keeps future geometry/material
combinations behind the same `run --config` interface.

## Plane release phase

An optional release phase is declared in the plane setup's `protocol`:

| Field | Requirement |
|---|---|
| `release` | `true` to enable unloading and separation |
| `release_start_time_s` | A keyframe time inside the recorded interval |
| `allow_recorded_lift_off` | `true` for the release phase |
| `keyframes` | After release starts, normal travel decreases monotonically to a negative value; x stays at the final slide position |
| `recorded_interval_s` | Includes the final lifted state |

Normal travel is measured from first touch, so negative final travel lifts the
specimen above that reference. Full macroscopic contact is required through the
release boundary. During unloading, separation is allowed while footprint,
edge-margin, force-balance, and projection checks remain active. Final release
requires contact force below 1 µN and elastic gel displacement below 10 nm.
Viscoelastic specimen deformation may remain after separation; it is not required
to recover instantaneously. The supplied comparison setup retains its original
contact-only protocol unless a custom setup explicitly adds release.
