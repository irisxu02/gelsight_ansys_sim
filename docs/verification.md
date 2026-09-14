# Verification and recorded results

[Documentation](README.md) · [Setup](getting-started.md) · [Architecture](architecture.md)

The current examples use a uniform Neo-Hookean gel and raw-style optical output.
Successful numerical checks establish implementation consistency, not a measured
match to a physical sensor or mesh convergence.

## Run the checks

```bash
python -B -m unittest discover -s tests -v
```

The suite includes portable CPU checks and opt-in CUDA checks. CPU checks cover
mesh geometry, material conversions, conservative force mapping, camera rays,
marker deformation, result identity, signed RGB differences, preservation of
the reference background, animation completeness, and diagnostic privacy.
Run the suite in the target environment to obtain its current test results.

Enable CUDA checks with `GELSIGHT_TEST_CUDA=1` in the Python process environment.
They compare both analytic and example-based RGB against CPU, within one uint8
intensity level per channel, including signed differences and deformed markers.

Licensed runs must be sequential when only one solver checkout is available:

```bash
python scripts/validate_simulation.py --case examples --output outputs/validation --allow-unlisted-gpu
python scripts/export_examples.py --validation outputs/validation/validation.json
python scripts/compare_examples.py
python scripts/audit_examples.py
```

Choose `--case press`, `slide`, `twist`, `soft`, or `rough` individually.
`formulations` explicitly compares displacement and mixed u-P on smaller models.
For native Linux MAPDL, supply `--exec-file /usr/ansys_inc/v252/ansys/bin/ansys252`;
licensed native Linux execution has not been verified.

## Current example results

The detached high-resolution queue discovers the curated sphere, imported-object,
and material-comparison presets.
Only passed cases are exported; see local `docs/examples/queue-status.json`.
Seven experimental plane-material cases use a separate solver path; native
coupon checks and full-recording acceptance are described in [plane validation](plane-material-adapters.md).
The separate locally refined 0.8 mm preview passed all six saved loading frames with active solver GPU acceleration
and CUDA rendering. At 0.8 mm the force was 0.36087 N, with a 0.68% force-balance
residual. It does not validate a full release, sliding, or twisting cycle.
[Preview and scope](rendering/README.md).

Each run requires solver convergence and requested load-step history, force
balance within 2%, conservative raster integration, zero released contact force
within 1 µN, and gel recovery within 10 nm. Deformable objects must also deform,
follow the grip motion, and recover. Sliding and twisting require nonzero shear
and torque; sphere twist also checks marker circulation direction.

Presets start with two internal substeps per load step. Automatic stepping can
refine to the configured minimum increment (1/200 of the load step). These
internal substeps are distinct from the saved trajectory frames.

The solver uses the L1 force convergence norm (`solver.force_norm=1`),
with tolerance 0.005. The sum of absolute residuals controls distributed residual
forces more directly than L2; the separate 2% global balance check remains in
place. This is a numerical criterion, not a material adjustment.
[ANSYS CNVTOL](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_cmd/Hlp_C_CNVTOL.html).

Plane runs resolve this value from their setup rather than substituting one, and
`--force-tolerance` / `--force-norm` vary it for a controlled sweep. Reading a
stalled solve and choosing these controls is covered in
[Convergence](convergence.md).

## Elastic-cube API check

`scripts/ansys_smoke_test.py` launches a fresh MAPDL process using gRPC, two CPU
cores in shared-memory mode, and one NVIDIA GPU by default. It creates a
10 mm cube with SOLID185 elements, Young's modulus 1 MPa, Poisson ratio 0.3,
and 0.1 mm prescribed top compression. All calculations use metres, newtons,
and pascals.

Symmetry constraints on the x=0, y=0, and z=0 planes remove rigid motion while
allowing lateral expansion. These are verification boundary conditions; they
do not represent a sensor's bonded backing.

For cube side length L and compression d, the independent reference is:

- Top reaction: `-E * L**2 * d / L = -1 N`.
- Axial displacement: `uz = -d * z / L`.
- Lateral displacement: `ux = nu * d * x / L`, `uy = nu * d * y / L`.

Acceptance requires relative force error at most `1e-5` and maximum absolute
nodal displacement error at most `1e-9 m`. On reaching result extraction, the
test writes `nodes.csv`, `solve.txt`, and a JSON summary. A command log and raw
solver files stay in the same timestamped run directory. The process is closed
in `finally`, and a failed check produces a nonzero exit status.

### Recorded cube result

The elastic-cube smoke test completed using the Python API.

| Check | Observed result |
|---|---|
| ANSYS / Python / PyMAPDL | 2025 R2 / 3.13.5 / 0.74.1 |
| Model | 27 nodes, 8 SOLID185 elements |
| Expected top reaction | -1 N |
| Computed top reaction | -1.0000000000000007 N |
| Relative force error | 6.66e-16 |
| Maximum displacement error | 4.07e-20 m |
| GPU | NVIDIA GeForce RTX 4070 SUPER |
| Device override | `ANSGPU_OVERRIDE=1`, set only in the test launcher process |
| Sparse solver marker | `GPU acceleration activated` |
| GPU-accelerated factorization flops | 100% reported in solver statistics |
| Smoke-test exit status | 0, passed |

Without the override, the same GPU was detected but rejected by ANSYS's
recommended-device check. The cube run establishes numerical correctness and GPU solver execution for
this small linear case. The nonlinear pipeline is checked separately above;
neither run establishes a speedup or experimental accuracy.
Detailed run artifacts are kept outside the repository; this table contains
only non-identifying validation results.

## GPU execution

The default allocation uses `-smp` with four CPU solver cores; surface and camera
projection and RGB rendering use CUDA. This allocation is faster on the tested
workstation. The CPU mechanics mode correctly records
`gpu_mechanics_verified=false`; `projection_device` and `render_device` identify
the independently used GPU. [Measured performance](performance.md).

`--solver-gpu` selects three CPU solver cores and `-smp -acc nvidia -na 1`.
In this mode device detection and requested switches are insufficient evidence:
acceptance requires a GPU activation marker and positive accelerated work in
`.dsp` statistics. A numerical pass can fail the requested-GPU check when no
accelerated work is confirmed. `--cpu` explicitly uses CPU for every stage.

Four additional CUDA projection checks cover full, clipped and empty FOVs,
arbitrary vector loads, millimeter-scale geometry, deformed camera mapping and
invalid-geometry rejection. Real saved-frame comparisons preserve force integrals
and surface geometry; CPU/CUDA rendered RGB agrees within one intensity level.

For nearly incompressible gel models, mixed displacement-pressure formulations
and some contact methods can require partial pivoting, which disables sparse
GPU acceleration in ANSYS 2025 R2. Formulation accuracy and GPU compatibility
are separate validation criteria. See [GPU-supported features](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_dan/gpusupport.html)
and [solver-statistics checks](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_dan/gputrouble.html).

### GPUs outside the recommended list

The CLI's `--allow-unlisted-gpu` option sets `ANSGPU_OVERRIDE=1` only for the
launched solver process. The tested RTX 4070 SUPER required it. This establishes
neither recommended-hardware status nor a speedup. See
[ANSYS GPU installation requirements](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/installation/win_gpureqt.html)
and [environment setup](getting-started.md#gpu-configuration).

The observed teaching entitlement includes four solver tasks. A GPU uses one
task; four CPU threads plus one GPU exceeded that allowance. Both four CPU solver threads and the optional three-CPU-plus-GPU
allocation fit it. The CUDA projection/rendering process is separate from ANSYS
solver licensing. Actual entitlements depend on the configured
license.

## Interpretation limits

A passed run has satisfied the configured numerical checks. It does not establish
mesh convergence, experimental material parameters, optical fidelity, or a GPU
speedup. The consumer GPU used for the recorded tests has limited FP64 throughput.
Reported elapsed time includes launch, solving, extraction, projection, rendering,
and exports.

The [modeling guide](modeling.md#accuracy-and-validation-limits) defines
numerical limitations, and the
[sensor alignment guide](sensor-alignment.md#device-calibration-requirements)
defines device-calibration data requirements.

## Mesh-import validation

Portable mesh checks run with the normal test suite. They cover ASCII/binary
STL, OBJ indices, SI conversion and transforms, surface and volume validity,
contact/grip selection, command numbering, embedded mesh replay, and frozen
external assets. The gel projection and optics continue to have CPU/CUDA checks.

For licensed checks, run the following when a solver license is available:

```bash
python scripts/validate_mesh_import.py --output outputs/mesh_import_validation
```

On Linux, supply the ANSYS executable through `--exec-file`. This command uses
one CPU solver core by default, CUDA optics, and a small 16 × 12 × 4 gel mesh.
It compares an imported quad target with the generated flat target, then checks
the closed STL and deformable JSON objects. It requires force balance, release
recovery, and prescribed grip motion. These engineering checks do not establish
mesh convergence and do not replace curated example exports. Review the saved
validation record for actual solver completion; configuration dry-runs and
command generation alone do not establish nonlinear convergence.

## Short contact diagnostics

Run isolated mechanical checks before a full example queue:

```bash
python scripts/benchmark_contact.py --output outputs/contact_diagnostics --variants baseline mixed_up projection softer --stop-time -1.7
```

This compares the same early rigid-plane preload using the baseline, mixed u-P
gel elements, standard surface-projection contact, and lower normal penalty
stiffness. Each variant changes one numerical choice. The results include saved
last states, forces, force-balance errors, timings, and failure logs; they are
private diagnostic artifacts and cannot replace validated example exports.
`--config` also accepts imported-object and flat-target presets; for those,
`--stop-time` selects the last trajectory pose to attempt. `--variants no_predictor
unified` separately tests disabled displacement prediction and unified contact
detection; `no_predictor` is refused for a plane preset, whose baseline already
solves with the predictor off. `--timeout-s` bounds each diagnostic's wall time. Surface-projection
contact should only be tested with faceted targets, not primitive spheres.
Successful short diagnostics do not establish a complete trajectory or mesh
convergence. Output-thinning tests separately check that mechanical checkpoints
remain unchanged and that failures between saved frames are still rejected.

For an isolated time-step and object-mesh comparison:

```bash
python scripts/benchmark_plane_speed.py --output outputs/plane_speed
```

Keep exploratory measurements and partial-run reports under `outputs/`.

Plane restart tests check preserved preload and frame history, rejection of changed
physics or missing restart files, and continuation without repeating completed
checkpoints. Release tests retain full-contact requirements before unloading and
footprint checks after separation becomes permissible. A licensed continuation
also compares the restored surface/body fields with the saved converged state
before applying the next load increment.
