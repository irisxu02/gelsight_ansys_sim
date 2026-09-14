# Mechanical and optical model

[Documentation](README.md) · [Materials and contact](materials-and-contact.md) · [Camera and markers](sensor-alignment.md)

## Constitutive model

The simulator uses **hyperelastic finite-element mechanics** for the gel.
The solid continuum model computes displacement throughout the gel, including
surface and marker motion outside the contact patch and the response to the
bonded backing.

The term *hydroelastic contact* commonly refers to efficient robotics contact
models that derive distributed forces from intersecting compliant pressure
fields. Those contact forces alone do not determine the gel's internal
deformation and material-marker motion.
This distinction follows the [Drake hydroelastic contact description](https://drake.mit.edu/doxygen_cxx/group__hydroelastic__user__guide.html).

Hyperelasticity describes reversible, finite-strain solid behavior. The
implemented Neo-Hookean model uses shear modulus `mu = E / (2(1+nu))` and
compressibility parameter `d = 2/K`, where `K = E / (3(1-2nu))`. Two-parameter
Mooney–Rivlin and linear elasticity are also selectable. The Mooney parameter
fraction splits `mu/2` into `C10` and `C01`. These are constitutive choices,
not fitted sensor properties. See [ANSYS hyperelastic theory](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_thry/thy_mat5.html).

Viscosity, adhesion, and fluid transport require distinct constitutive or
interface laws. They are outside the current gel model.

## Implemented geometry and boundary conditions

- Rectangular gel, nominally `25.25 x 20.75 x 4 mm`, with a structured hexahedral mesh.
- All three translations fixed at the bottom: a perfectly bonded rigid backing.
- Free sides and exposed upper surface.
- SOLID185 finite-strain continuum elements, with displacement or mixed u-P
  formulation selected by configuration.
- Silicone coating homogenized into the gel, with no separate mechanical layer.
- Rigid analytic sphere of radius 3 mm, finite rectangular flat target, or a deformable sphere with a translating upper grip.
- Imported rigid surfaces and deformable hex volumes, plus finite rigid or
  deformable slabs through the plane adapter.
- CONTA174/TARGE170 augmented-Lagrangian contact and Coulomb friction, initially
  `mu_friction = 0.5`.
- Prescribed x/y translation, indentation, and rotation about the gel z-axis.
  General-contact pilot degrees of freedom are constrained by the configured
  pose; deformable sphere and imported-volume grips support translation.
  Plane recording can instead apply a normal load to a platen free to move in z.

The plane adapter adds velocity-dependent and custom directional/adhesive
contact laws, as described in [plane materials](materials-and-contact.md#plane-material-specifications).

See [materials and contact](materials-and-contact.md) for the coating simplification,
object grip conditions, and numerical friction controls.

The default `E=100 kPa, nu=0.49` gives finite, near-incompressible elasticity.
The displacement formulation supports the tested GPU path. Mixed u-P is
available for near-incompressibility studies; it can trigger partial pivoting
and prevent GPU sparse factorization in ANSYS. GPU execution does not establish
material accuracy or mesh convergence. See [SOLID185](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_elem/Hlp_E_SOLID185.html)
and [GPU-supported features](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_dan/gpusupport.html).

All deeper-loading examples select `solver.newton_raphson="unsymmetric"`
to retain the frictional tangent terms during stick/slip transitions. The
`"full"` option remains available for symmetric tangents. GPU execution with both choices was verified through solver
statistics; see [NROPT](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_cmd/Hlp_C_NROPT.html).

The `linear` material option retains geometric nonlinearity and provides a
constitutive comparison. All curated presets use Neo-Hookean gel elasticity;
plane specimens can additionally use hyperfoam or effective fabric laws.

## Loading convention

Depth is prescribed indenter or grip travel into the sensor, or, for a plane
setup that selects `normal_control: prescribed_normal_force`, travel becomes the
outcome of a commanded load. For a deformable object, that travel is divided
between the object and the gel. Time is a quasi-static load parameter for the
sphere presets: no inertia and no rate-dependent material law.

Plane setups may relax both. A material case can carry Prony relaxation, which
makes time physical, and `protocol.transient` can switch inertia on across a
named window. The shipped plane suite enables inertia during sliding, and the
rubber, foam, and fabric cases enable relaxation. Neither is used by the shipped
sphere or imported-object presets.
See [convergence](convergence.md) for when a window is needed and how to size
one.
See [example trajectories](examples/README.md#trajectory-conventions) for
individual motions and [solver lifecycle](architecture.md#solver-lifecycle-and-contact-results)
for nonlinear continuation and result checks.

## Surface and optics

Force and deformation fields use an orthographic gel-plane grid, which preserves
physical units and conservative force integrals. Tactile RGB uses a separate
rectified pinhole camera in the nominal Mini examples. Perspective-correct
triangle interpolation maps each camera ray to both its deformed surface point
and its original material coordinates. Optical fields and marker centers use the
same projection; inward motion toward the camera produces apparent dilation.

Markers have fixed barycentric attachments and move with the ANSYS surface,
including outside contact. These are gel-surface material attachments. Antialiased disks are evaluated in a reference
material texture. A disk stretches or bends when its material deforms; it is not
redrawn as an unchanged disk at a translated center. No extra smoothed shear law
or empirical marker-motion gain is applied to the finite-element displacement.
The older Gaussian stamping mode remains explicitly selectable.

The default renderer evaluates a TAXIM-style polynomial RGB response using the
solved normal and its pixel position. It subtracts the flat-normal response and
adds a measured, marker-free example-sensor background. The portrait calibration
and background are rotated into the landscape camera convention. The angular response table is smoothed (sigma 2 bins) and cubically
interpolated with a continuous periodic seam. The default response gain of
2 is an appearance adjustment. A separately selectable `optics.model="analytic"`
retains configurable diffuse/specular lights.

Raw RGB is the default. `--subtract-background` instead displays the signed
change from the first unloaded image, including reference markers. The displayed
uint8 encoding is `round(128 + (raw - reference)/2)`; exact differences are saved
as int16. These images derive from surface orientation, not a pressure colormap.
CPU and CUDA evaluate the same optical response and material texture.
See [asset provenance](../src/gelsight_ansys/data/mini/PROVENANCE.md) and the
[sensor alignment audit](sensor-alignment.md). Borrowed optical assets do not
calibrate this sensor's geometry, silicone, lighting, or friction.

## Accuracy and validation limits

The default configuration and all curated presets use a uniform 36 × 30 × 8 gel mesh
(8,640 elements), with approximately 0.70 × 0.69 × 0.50 mm cells.

For general-contact runs, `--refine-contact` enables the optional mesh used in
the saved loading preview. Plane gel refinement is selected through the setup's
`discretization.gel_mesh`, as described in [plane mesh controls](plane-material-adapters.md#numerical-controls-and-resources).
Within x = ±3.6 mm and y = ±3.0 mm, surface cells are 0.3 × 0.3 mm. This region
covers the prescribed contact and sliding paths. Geometric transitions coarsen
the outer gel, with adjacent in-plane cell-size ratios below 1.6.
Through-thickness bias gives a top layer about 0.178 mm thick and a bottom layer
about 1.023 mm thick. The nominal element count remains 8,640.
See [mesh selection](usage.md#optional-local-contact-refinement).

Local refinement is a resolution choice, not proof of convergence. Relevant
sensitivities include further contact-region and thickness refinement, object
surface resolution, element formulation, contact stiffness, and load increments.
Finer camera pixels do not refine contact mechanics.
[ANSYS contact sizing](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/wb_msh/ds_Contact_Sizing.html)
supports local interface sizing; this structured MAPDL mesh is generated before
adding contact elements.

The optical table has its own smoothing control. Refining the mechanical mesh
cannot remove irregularities in an optical calibration table, and optical
smoothing cannot validate mechanical resolution.

Mechanical calibration depends on measured force–depth and surface
displacement data. Friction and optical calibration require separate interface
and imaging measurements.
The current implementation excludes direct CAD/B-rep import, tilt about x/y,
coating damage, lens distortion, self-contact, and folded/overhanging surfaces.
The gel's constitutive law stays elastic and rate-independent in every preset.
On the plane path, `viscoelasticity` adds specimen relaxation and
`contact.adhesion` adds an adhesive interface through a native adapter.
`protocol.transient` can integrate the mass of the gel and deformable specimen;
the shipped suite enables it during sliding. The renderer rejects reversed
surface triangles instead of silently producing an invalid height image.
