# Material-plane solver

The seven material comparison configs use the shared `run --config` entry point.
Selecting `object.geometry.shape = "plane"` chooses the finite-slab ANSYS adapter
and its physical-time recording and CUDA rendering path. They are experimental models. A successful run means that the prescribed
trajectory and numerical acceptance checks completed; it does not establish
material calibration or mesh convergence. Local queues write
`docs/examples/queue-status.json` for dataset status; that generated file is
excluded from Git. Published previews and their scope are in the
[example gallery](examples/README.md).

```bash
python scripts/run_simulation.py run --config configs/material_plane_slide/compressible_foam.json --render-scale 4
# Equivalent installed entry point:
gelsight-ansys run --config configs/material_plane_slide/compressible_foam.json --render-scale 4
```

See [configuration composition](configuration.md) for material cases and parameter
overrides. Fabric and sticky contact also require `--libraries PATH_TO_NATIVE_LIBRARIES`.
The native libraries are loaded only by the launched ANSYS process, through
`ANS_USER_PATH`. Nothing is installed into the ANSYS directory. Build them with
`scripts/build_plane_adapters.py --compiler PATH_TO_MINGW_GCC --output outputs/plane_adapters`.
The build manifest records source and binary hashes. Compiler success alone is
not a native-solver validation.

## Mechanics and recording

All cases retain the uniform 100 kPa, ν=0.49 Neo-Hookean gel. The
silicone coating remains mechanically homogenized into that gel. The finite
60 × 35 mm specimen is 3 mm thick, except for the 10 mm foam slab. Its top face
follows the platen and its sides are free. Initialization prescribes 0.030 mm
normal travel; recording ramps normal load from 1 to 5 N, then holds 5 N during
the holds and prescribed lateral slide. Normal platen travel is read from the
solve under load control.
Rubber, foam, and fabric deform; reference, slippery, rough, and sticky specimens
are rigid. Roughness is explicit moving target geometry.

The preload runs from physical time −2 to 0 s. Recording contains 311 frames
from 0 to 3.1 s over 362 mechanical checkpoints. The maximum internal time
increment is 0.01 s outside the 2.29–2.8 s transient window and 0.0001 s inside
it. The window integrates mass and uses 0.005 s mechanical checkpoints;
saved frames remain spaced at 0.01 s. The remaining protocol is quasi-static.
Restart files retain Prony and contact history across load steps. An unloaded
state and image are stored separately: recorded frame 0 is already preloaded.

| Material/interface | ANSYS implementation |
|---|---|
| Soft rubber | SOLID185, mixed displacement–pressure Neo-Hookean elasticity and shear Prony branches |
| Compressible foam | SOLID185, Ogden FOAM elasticity and shear/bulk Prony branches |
| Effective fabric | SOLID185, USERHYPERANISO energy, orthotropic elastic coupling, monotone compaction curve, and Prony relaxation |
| Isotropic friction | CONTA174 augmented Lagrange, finite sliding, native exponential velocity-dependent Coulomb friction |
| Fabric friction | USERINTER elliptical yield surface with associated sliding, in the deformed specimen fiber basis |
| Sticky contact | USERINTER repulsion plus reversible triangular attraction and attachment-dependent cohesive shear |

Fabric is an effective continuum, without individual fibers. Its energy uses
axial stretches λᵢ = √Cᵢᵢ, reciprocal orthotropic normal stiffness, and shear
terms Gᵢⱼ Cᵢⱼ²/(2λᵢλⱼ). A monotone integral of the specified nominal compression
curve replaces the uncoupled z compression energy. The ANSYS adapter evaluates
this energy through three I4 invariants, three I5 invariants, and J, with exact
first and second derivatives. Compression beyond the supplied 70% curve is
rejected. These choices define a provisional finite-strain extension of the
specified small-strain parameters; experimental validation remains necessary.

The custom contact law uses repulsive pressure for Coulomb friction. Attraction
is 5 kPa at zero gap and decreases to zero over 20 µm, giving 0.05 J/m² of
separation work. Cohesive shear is 3 kPa multiplied by attached fraction.
Attachment heals on recontact. Contact history and ANSYS integration areas are
exported through USEROU. Friction coefficients depend on actual plastic slip
per physical time increment.

## Numerical controls and resources

The default allocation is four ANSYS CPU cores and CUDA surface projection and
optics. `--solver-mode specified` preserves the suite's requested solver GPU
allocation. GPU mechanics must be verified if requested; unsupported solver
formulations are reported as failures.

Plane runs take force convergence settings from the setup: the shipped values
are tolerance 0.005, the L1 norm (`force_norm: 1`), and a ceiling of
150 Newton iterations. They disable displacement prediction and allow automatic
bisection. `--force-tolerance` and `--force-norm` support controlled overrides;
resolved settings are recorded in `config.json`. Material parameters, prescribed
loads/motion, and acceptance thresholds are preserved by these numerical options.

The default gel is the standard uniform **36 × 30 × 8 mesh**: 10,323 nodes
and 8,640 solid elements, using `sensor.gel.elements` from the setup.
Set `discretization.gel_mesh` to `"refined"` in the setup to opt into the
0.125 mm surface grid, 0.5 mm refined depth and depth growth at most 1.3
(440,713 nodes and 402,384 elements). The default is `"uniform"`.
This changes discretization only; gel dimensions and material parameters stay the same.
Texture response and forces require a mesh-convergence comparison.
Object simplification is enabled by default:

| Object | Matched object mesh | Simplified object mesh |
|---|---:|---:|
| Smooth rigid plane | 135,161 target nodes; 134,400 facets | 4 target nodes; 1 exact planar facet |
| 3 mm deformable slab | 1,621,932 nodes; 1,478,400 solid elements | 42,955 nodes; 33,600 solid elements |
| Textured rigid plane | 135,161 nodes; 134,400 facets | Preserved to resolve the specified roughness |

The deformable object uses 0.5 mm surface cells and graded thickness. This
reduces the 3 mm slab's solid element count by 44×; counts vary with specimen
thickness and mesh overrides. Geometry, material laws, specimen
thickness, and gel discretization are preserved. `--object-mesh matched`
selects the matched object grid for comparisons. `--object-element-size-m`
sets an independent deformable-object size, for example 0.00025 m for a finer
object check. These controls affect plane objects only.

A native teaching-license capacity probe accepted a connected model with
600,000 nodes and 599,999 elements. This checks model-size acceptance, not
memory or runtime for a nonlinear three-dimensional solid. The simplified
production meshes remain below those tested counts.

`--pilot-element-size-m VALUE` explicitly selects a coarse engineering probe;
`--stop-after-s TIME` stops a probe early. Such runs are marked `pilot_passed`
and cannot replace full production examples. A completed run still needs
separate through-thickness and locking convergence studies.

## Data integrity and validation

The binary reader is pinned to `ansys-mapdl-reader==0.56.0`. Plane results use
`/FCOMP,RST,0`: compressed nodal records produced incorrect values in the legacy
reader. Five uncompressed native result sets matched MAPDL's own nodal
postprocessing exactly. Contact element force extraction was independently
compared with MAPDL FSUM on existing sphere results, with differences below
5 × 10⁻¹⁴ N. Contact-point statuses use NMISC 1–4; NMISC 41 is the element maximum.

Portable constitutive tests check the compression curve, energy derivatives,
friction ellipse, sliding rate, contact-basis rotation, and reversible adhesion.
Native element coupons have verified rubber/foam relaxation, fabric compression
and relaxation, and all three fabric shear planes. Native small contact probes
have verified repulsion/adhesion and USEROU integration-area output. The nine-state
prescribed-gap coupon additionally passed opening, attractive traction, repulsive
superposition, sliding, separation, and reattachment checks. Changes to
custom contact callbacks require rerunning the native contact tests.

Every recorded substep must satisfy geometric coverage, specimen edge margin,
and the protocol's required-contact conditions. Contact/backing force balance
is required outside inertia windows; inside them the residual is recorded.
Recorded frames additionally check pilot load and conservative surface-to-image
force integration and preserve fixed material-marker identities. See the
[dataset acceptance rules](dataset.md#metrics-and-acceptance).
This is distinct from constitutive calibration and mesh convergence.

Tactile PNGs and signed RGB differences are 1280 × 960 at render scale 4; physical
force fields use the nominal 320 × 240 grid. Physical FOV and marker attachments
are unchanged. Optical geometry can be reconstructed from saved surface states.
GIF generation streams frames with a bounded palette sample, so a long recording
does not require a full-resolution in-memory atlas.

## References

- [ANSYS hyperelastic materials](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_mat/aQw8sq22dldm.html)
- [ANSYS USERHYPERANISO](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_prog/Z7K4r1e5lcd.html)
- [ANSYS custom contact interfaces](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_prog/upfcontinterfac.html)
- [ANSYS contact controls](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_ctec/Hlp_ctec_realkey.html)
- [PyMAPDL reader compression compatibility](https://github.com/ansys/pymapdl-reader)
