# Example object meshes

These source meshes were generated for this repository; they are inputs, not
simulation results. The block meshes describe a 3 × 3 × 2 mm rectangular block
with its base at source z=0. The pyramid has a 6 × 6 mm square base at z=3 mm
and its tip at z=0. Configs declare millimeters explicitly.

- `pyramid_surface.stl`: closed outward-oriented square pyramid, 6 triangles.
  A standalone sample of a sharp feature; no shipped preset uses it, because
  on the uniform gel mesh its tip is carried by one or two elements and a
  slide is lost within the first millimetre. It needs a gel refinement
  comparison before it can be more than an import example.
- `block_surface.stl`: closed outward-oriented surface, 12 triangles.
- `block_hex.json`: 48 eight-node hex elements, an outward bottom `contact` face
  set, and a top `grip` node set. These small meshes demonstrate importing and
  fixture selection; their resolution does not establish mesh convergence.

## Indenters

Printed indenters on their mounting plates, in millimeters, with the contact
feature facing +z:

| File | Feature | Radius | Feature position |
|---|---|---|---|
| `cylinder-large.stl` | Cylinder along x, 45 mm long | 50 mm | ridge at y=0, z=50 |
| `cylinder-small.stl` | Cylinder along x, 45 mm long | 10 mm | ridge at y=-22.5, z=10 |
| `hemisphere-large.stl` | Spherical cap | 50 mm | tip at (0, 0, 50) |
| `hemisphere-small.stl` | Spherical cap | 10 mm | tip at (0, 0, 15.85) |

`cylinder-r10-fine.stl`, `cylinder-r50-fine.stl`, `hemisphere-r10-fine.stl`
and `hemisphere-r50-fine.stl` are finer tessellations of `cylinder-small`,
`cylinder-large`, `hemisphere-small` and `hemisphere-large`, with the same
bounds and placement. The two hemispheres have facets about 0.18 and 0.85 mm
across at the tip, against 0.44 and 2 mm before.
`cylinder-r50-fine.stl` keeps the plate edges described below and is rejected
the same way; `cylinder-r50-fine-top.stl` is its top arc, cropped by the same
rule: 108 triangles, 54 facets 0.86 mm wide that stand at most 1.9 µm off the
cylinder.

`cylinder-large.stl` has two edges shared by four faces, where the pedestal
walls meet the plate at y=±22.5, z=34.15, so the importer rejects it.
`cylinder-large-top.stl` keeps its 44 triangles with every vertex at z ≥ 44.6:
the whole R50 arc, unchanged, and 5.4 mm of relief above first touch, beyond
the 4 mm contact pinball. The arc is faceted at 2.43°, facets 2.1 mm wide
that stand at most 11 µm off the true cylinder.

See [custom meshes](../../docs/configuration.md#custom-object-meshes) for the
file schema, units, transforms, and supported material/motion combinations.

Runnable presets:

- [Rigid block press](../../configs/imported_rigid_press.json): 0.3 mm indentation and release.
- [Soft block press](../../configs/imported_soft_press.json): 50 kPa Neo-Hookean hex volume.

Follow [use your own mesh](../../docs/usage.md#use-your-own-object-mesh) to replace
the object while keeping the same simulation and rendering workflow.
