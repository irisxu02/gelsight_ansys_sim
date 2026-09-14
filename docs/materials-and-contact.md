# Materials and contact

[Documentation](README.md) · [Model assumptions](modeling.md)

## Sensor material

Every preset uses a uniform, compressible Neo-Hookean gel with small-strain
Young's modulus **100 kPa** and Poisson ratio **0.49**. Sphere, imported-object,
and plane presets share these gel constants.
The pad is 25.25 × 20.75 × 4 mm; its backing is fixed and its sides are free.
These dimensions and material constants are nominal, not a fit to a sensor.

**Coating simplification:** the fabricated black silicone coating is
treated mechanically as part of this uniform gel. There is no separate coating
mesh, modulus, thickness, bending stiffness, prestress, or interface law.
Its optical appearance is represented by the renderer. This is a modeling
approximation, not evidence that the coating has zero mechanical influence.

| Component | Constitutive model | Small-strain E | Poisson ratio | Object/gel friction |
|---|---|---:|---:|---:|
| Gel, all curated presets | Neo-Hookean | 100 kPa | 0.49 | — |
| Rigid sphere | Rigid | — | — | 0.5 |
| Soft sphere | Neo-Hookean | 50 kPa | 0.45 | 0.5 |
| Rough-slide sphere | Neo-Hookean | 2 MPa | 0.45 | 0.9 |

All material and friction values are provisional. A material name in an example
is a controlled simulation choice, not identification of a commercial silicone.
The gel constants give shear modulus 33.557 kPa and bulk modulus 1.667 MPa.
Neo-Hookean ANSYS input uses `mu = E/(2(1+nu))` and `D = 2/K`.
Mooney–Rivlin and linear options remain available for explicit material studies;
the curated presets use the Neo-Hookean gel law.

## Friction and object compliance

The sphere and imported-object presets use CONTA174/TARGE170 with finite sliding,
augmented-Lagrange normal contact, and Coulomb friction. Contact can stick,
slide, and separate.
`KEYOPT(18)=0` permits finite sliding and `KEYOPT(10)=2` updates contact stiffness
on each iteration. Default FKN and FKT factors are 1, normal penetration
tolerance is 1 µm, and tangential elastic-slip tolerance is 5 µm.
The latter allows numerical slip while sticking; it is not a material shear
law. Integration-point status and ELSI are saved for sensitivity studies.
[ANSYS contact controls](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_ctec/Hlp_ctec_realkey.html).

Rigid spheres have an analytic 3 mm radius surface. Deformable spheres have a
conforming hexahedral mesh with 12 elements per cube axis, mapped into a sphere.
Their upper cap (reference z at least 0.5 radius above the center) follows the
prescribed translation. Contact uses the deformed, faceted object surface.
The general adapter also supports a configurable rigid rectangular flat target;
there is no dedicated flat-target preset in the current catalog.

Depth is prescribed object/grip travel from nominal first touch. A soft object
absorbs some of that travel, so a 1 mm command is not 1 mm of gel indentation.
Whole-body object displacements and grip-motion errors are exported.
The rough-slide example changes both sphere stiffness and friction. It does not
resolve microscopic roughness and does not use ANSYS's no-slip “rough” option.
Rigid and deformable surfaces also differ in discretization; their comparisons
are illustrative, not an isolated calibrated material experiment.

## Plane material specifications

The [plane press-and-slide suite](examples/README.md#plane-press-and-slide-specifications)
defines seven experimental material cases. The finite-slab mechanical adapter
implements their finite slabs, material histories, friction, and coverage checks.
See [the adapters and validation scope](plane-material-adapters.md) and
local `docs/examples/queue-status.json`. All parameter values remain
synthetic and uncalibrated.

Every case uses the same uniform sensor gel described above. Only specimen bulk
behavior, surface geometry, thickness, and interface properties vary. The specimen
is a 60 × 35 mm slab with a nominally planar contacting face, 3 mm thick except
for the 10 mm foam slab. Every case is pressed to the same commanded normal
load, 5 N, and the platen travel that
takes is recorded rather than set: it includes both specimen and gel
compression, so the same load means a different indentation for each material,
which is the comparison the suite exists to make. The complete geometry,
trajectory, contact requirements, and GPU policy are in
[`suite.json`](../configs/material_plane_slide/suite.json).

### Shared interface and relaxation

The implemented interface uses finite sliding and allows local separation. Most
cases share static friction coefficient **0.60**, limiting kinetic coefficient
**0.45**, and decay speed **1 mm/s**:

```text
mu(v) = mu_k + (mu_s - mu_k) * exp(-abs(v) / v_decay)
```

Here `v` is local relative slip speed, which can differ from commanded platen
speed while the gel and specimen deform. The kinetic coefficient is approached
gradually; it is not an immediate switch at sliding onset. Friction belongs to
the specimen–sensor surface pair. These settings differ from the constant
coefficients used by the sphere and imported-object presets.
[ANSYS velocity-dependent friction](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_ctec/Hlp_ctec_realkey.html).

Rubber, foam, and fabric include generalized Maxwell/Prony relaxation. Rubber
and foam use native ANSYS hyperelastic laws; fabric combines a custom
USERHYPERANISO energy with Prony branches. Each fraction is relative to
instantaneous stiffness:

| Case | Shear fractions at 0.2 s / 2 s | Bulk fractions at 0.2 s / 2 s | Long-term shear / bulk fraction |
|---|---|---|---|
| Soft rubber | 0.10 / 0.15 | 0 / 0 | 0.75 / 1.00 |
| Compressible foam | 0.20 / 0.25 | 0.15 / 0.15 | 0.55 / 0.70 |
| Fluffy fabric | 0.20 / 0.25 | 0.15 / 0.15 | 0.55 / 0.70 |

A relaxation time is the time constant of one decay contribution. These
fractions describe material moduli; they do not predict an identical percentage
drop in the assembled sensor force. Loading history, geometry, friction, and
redistribution affect that force. The preload history must carry into recording.
[ANSYS viscoelasticity](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_mat/evis.html).

### Rigid reference

[`rigid_reference.json`](../configs/material_plane_slide/rigid_reference.json)
defines a rigid, smooth specimen with the shared friction law and no adhesion.
There is no specimen deformation or material relaxation. Its purpose is to
provide a common reference for changes in compliance, friction, texture, and
adhesion; it does not identify a particular metal or plastic.

The expected signals are gel deformation, normal force during pressing, and
tangential force and marker motion during sliding. The interface can stick and
then slip as its local shear limit is reached.

### Soft rubber

[`soft_rubber.json`](../configs/material_plane_slide/soft_rubber.json) specifies
a nearly incompressible Neo-Hookean solid with instantaneous **E = 50 kPa**,
**ν = 0.49**, and density **1,100 kg/m³**. Its small-strain Young's modulus is
half the sensor's, but the assembled indentation stiffness also depends on
geometry and constraints.

The material stores deformation while the Prony contributions permit
time-dependent stress relaxation. Its long-term shear modulus approaches 75%
of the instantaneous value; bulk stiffness does not relax. Under load control,
platen travel includes specimen and gel compression. The resulting gel
deformation depends on the coupled contact solution; equal normal load alone
does not determine its difference from the rigid reference. The holds maintain
the commanded load while travel, shear force, and marker motion can evolve.

Friction and surface geometry match the rigid reference. The specified mixed
u-P formulation runs with CPU mechanics by default. Requested solver GPU use
must produce activation and accelerated-work evidence; the execution contract
rejects unsupported combinations without changing the material or formulation.

### Compressible foam

[`compressible_foam.json`](../configs/material_plane_slide/compressible_foam.json)
specifies a one-term Ogden hyperfoam model with **μ = 5 kPa**, **α = 2**,
**β = 1/3**, and density **150 kg/m³**. These coefficients govern stiffness,
nonlinear response, and compressibility. Hyperfoam represents substantial
volume change during compression.
[ANSYS hyperfoam and hyperelastic models](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_mat/aQw8sq22dldm.html).

The long-term shear and bulk stiffness fractions are 55% and 70%.
Expected observables include specimen compression and volume change, force
versus backing travel, and relaxation during holds. Its surface and friction
match the rigid reference.

This recoverable effective material does not resolve foam cells, permanent
crushing, or air/fluid transport. Native element coupons exercise the ANSYS
hyperfoam/Prony implementation; see [validation scope](plane-material-adapters.md#data-integrity-and-validation).
Those checks do not establish specimen calibration or a completed full slide.

### Fluffy fabric

[`fluffy_fabric.json`](../configs/material_plane_slide/fluffy_fabric.json)
defines an effective orthotropic layer: a continuum approximation to the
average behavior of many fibers. The x direction follows the slide and preferred
fiber orientation; y is transverse and z is through the thickness.

| Parameter | Value |
|---|---:|
| Instantaneous Young's modulus, x / y / z | 30 / 15 / 10 kPa |
| Instantaneous shear modulus, xy / xz / yz | 5 / 2 / 4 kPa |
| Poisson ratios, xy / xz / yz | 0.10 / 0.10 / 0.10 |
| Density | 80 kg/m³ |
| Static / limiting kinetic friction, x | 0.60 / 0.45 |
| Static / limiting kinetic friction, y | 0.90 / 0.70 |

Reciprocal Poisson ratios are derived from symmetric compliance. A tabulated
thickness-compression branch replaces the linear z-compression response:

| Engineering compression | Nominal compressive stress |
|---|---:|
| 0% | 0 kPa |
| 5% | 0.5 kPa |
| 15% | 1.6 kPa |
| 30% | 4 kPa |
| 50% | 15 kPa |
| 70% | 60 kPa |

The curve represents increasing resistance as the layer compacts. Extrapolation
outside the tabulated range is rejected. Directional friction and stiffness
are intended to affect shear and marker motion.

Individual hairs, strand bending/contact, and fiber impressions are not
resolved; the contact face is a homogenized planar envelope. The implemented
finite-strain energy combines orthotropic coupling and shear terms with the
integrated compression curve, and ANSYS supplies Prony relaxation. Its
[native adapter](plane-material-adapters.md#mechanics-and-recording) requires
the built libraries. Element coupons cover compression, relaxation, and all
three shear planes; the x-only full-slide protocol does not assess directional
sliding along y. The effective material remains uncalibrated.

### Slippery surface

[`slippery_surface.json`](../configs/material_plane_slide/slippery_surface.json)
keeps the rigid reference's geometry and bulk behavior and changes only the
friction coefficients to **μ_s = 0.12** and **μ_k = 0.08**. Decay speed remains
1 mm/s; there is no adhesion.

Expected effects are earlier slip, lower tangential resistance, and generally
less gel drag during sliding. Friction can also affect pressing by changing
resistance to lateral gel expansion. No lubricating liquid film or
hydrodynamic lubrication is represented.

### Rough surface

[`rough_surface.json`](../configs/material_plane_slide/rough_surface.json)
keeps the reference's rigid bulk and friction and adds geometric texture:

| Direction | Sinusoidal amplitude | Wavelength |
|---|---:|---:|
| x | 20 µm | 7.2 mm |
| y | 10 µm | 6.0 mm |

The two modes have zero phase, are summed in specimen material coordinates,
and move with the specimen. First touch is referenced to the highest surface
feature. Expected signals include spatial pressure variations, texture
impressions, and evolving marker motion during sliding.

This deterministic height field is a synthetic texture; measured topography
is needed to represent a particular specimen. The textured rigid target retains a 0.125 mm surface grid.
All seven cases default to the standard uniform 36 × 30 × 8 gel. The current
wavelengths span about 10 and 9 gel elements, respectively, meeting the declared
minimum of eight. This resolves gentle waviness; finer texture needs a finer
gel mesh. Compare against the optional `discretization.gel_mesh: "refined"`
setting before interpreting texture response quantitatively.

ANSYS supports arbitrary rigid target geometry using triangular or
quadrilateral TARGE170 segments. This permits geometric texture in mechanical
contact. The plane adapter constructs this target and translates it with the specimen.
[ANSYS target surfaces](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_ctec/Hlp_ctec_targsur.html).

### Sticky surface

[`sticky_surface.json`](../configs/material_plane_slide/sticky_surface.json)
keeps the reference's rigid, smooth specimen and friction, and adds an implemented
reversible adhesive interface:

| Parameter | Value |
|---|---:|
| Maximum tensile attraction | 5 kPa |
| Work of adhesion | 0.05 J/m² |
| Attraction cutoff gap | 20 µm |
| Additional cohesive shear strength at full attachment | 3 kPa |

For gaps from zero to 20 µm, tensile attraction decreases linearly from 5 kPa
to zero. The area under that traction–gap law is the stated work of adhesion.
Repulsive contact handles penetration. The shear limit combines friction
based on **repulsive** contact pressure with the attached fraction of the
3 kPa cohesive shear strength. Attachment recovers on recontact; irreversible
damage is excluded.

The expected sliding signatures are increased shear resistance and altered
marker drag. USERINTER implements the law, and native contact coupons cover
opening, attraction, repulsion, sliding, separation, and reattachment. These
checks are distinct from a completed material-comparison trajectory or
experimental calibration. Ordinary bonded contact does not implement this law. The suite never
releases the plane, so it cannot demonstrate pull-off strength or detachment.

### Scope and calibration data

The [completed rigid 1 mm slide](examples/README.md#rigid-plane-1-mm-slide-at-5-n)
provides one diagnostic result. It does not establish the expected differences
across all seven materials over the shipped 10 mm protocol. Smooth surfaces in
full-area contact may produce similar RGB images; force, marker displacement,
and their time evolution can carry stronger differences. The sequence has no
release, one slide direction, and one commanded slide speed, so it cannot
identify every material parameter.

The configuration files identify the experimental data associated with each
model parameter group:

| Parameter group | Calibration data |
|---|---|
| Specimen stiffness and compressibility | Compression and lateral-strain measurements |
| Relaxation | Force or stress histories during timed holds |
| Friction | Sliding against the sensor surface at multiple loads and speeds |
| Fabric anisotropy | Directional shear and friction measurements |
| Surface topography | Spatially resolved surface height map |
| Adhesion | Pull-off, shear, and recontact measurements |

No specimen-specific calibration datasets are included.

Densities supply mass to the gel and deformable specimens. The shipped suite
integrates inertia from 2.99 to 5.1 s with a maximum time increment of 0.1 ms;
the rest of the protocol is quasi-static. See [transient controls](convergence.md).
Numerical convergence, contact coverage, and experimental calibration remain
separate requirements.

## Sensor calibration status

The effective gel constants and interface friction are uncalibrated.
Force–depth and surface-displacement data characterize the gel response;
sliding and twisting measurements characterize the interface response.
Separate coating properties and interfaces are outside the implemented model.

The sphere presets remain rate-independent and quasi-static. The plane
solver adds the stated relaxation, reversible adhesion, and optional inertial
windows. Neither path includes coating damage or bulk plasticity.
