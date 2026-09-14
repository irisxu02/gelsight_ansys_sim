# Example object meshes

These source meshes were generated for this repository; they are inputs, not
simulation results. The block meshes describe a 3 × 3 × 2 mm rectangular block
with its base at source z=0. The pyramid has a 6 × 6 mm square base at z=3 mm
and its tip at z=0. Configs declare millimeters explicitly.

- `pyramid_surface.stl`: closed outward-oriented square pyramid, 6 triangles.
- `block_surface.stl`: closed outward-oriented surface, 12 triangles.
- `block_hex.json`: 48 eight-node hex elements, an outward bottom `contact` face
  set, and a top `grip` node set. These small meshes demonstrate importing and
  fixture selection; their resolution does not establish mesh convergence.

See [custom meshes](../../docs/configuration.md#custom-object-meshes) for the
file schema, units, transforms, and supported material/motion combinations.

Runnable presets:

- [Rigid block press](../../configs/imported_rigid_press.json): 0.3 mm indentation and release.
- [Soft block press](../../configs/imported_soft_press.json): 50 kPa Neo-Hookean hex volume.

Follow [use your own mesh](../../docs/usage.md#use-your-own-object-mesh) to replace
the object while keeping the same simulation and rendering workflow.
